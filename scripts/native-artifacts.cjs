// Native artifact provenance, reuse, and release publication. No compiler is run here.
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const TARGETS = ['aarch64-unknown-scarlet', 'riscv64gc-unknown-scarlet'];
const COMMON = ['flake.nix', 'flake.lock', 'nix/', 'scripts/native-artifacts.cjs',
  '.github/workflows/build.yml', '.github/workflows/native-toolchain-release.yml'];
const RELEASE_ONLY = new Set(['scripts/native-artifacts.cjs',
  '.github/workflows/build.yml', '.github/workflows/native-toolchain-release.yml']);
const INPUTS = {
  host: [...COMMON, 'native-host/', '.github/workflows/native-host.yml',
    'scripts/build-native-host.sh', 'scripts/prepare-native-host.py', 'scripts/check-native-host-llvm.py'],
  linker: [...COMMON, 'native-linker/', '.github/workflows/native-linker.yml',
    'scripts/build-native-linker.sh'],
};

function fingerprint(component, tree, buildInputsOnly = false) {
  if (!INPUTS[component]) throw new Error(`Unknown native component: ${component}`);
  const inputs = INPUTS[component].filter(input => !buildInputsOnly || !RELEASE_ONLY.has(input));
  const entries = tree.filter(entry => entry.type !== 'tree' && inputs.some(input =>
    input.endsWith('/') ? entry.path.startsWith(input) : entry.path === input))
    .map(({ mode, type, sha, path }) => [mode, type, sha, path])
    .sort((a, b) => Buffer.compare(Buffer.from(a[3]), Buffer.from(b[3])));
  if (!entries.length) throw new Error('Native source input tree is empty');
  return crypto.createHash('sha256').update(JSON.stringify(entries)).digest('hex');
}

function localTree() {
  return execFileSync('git', ['ls-tree', '-r', '-z', 'HEAD'], { encoding: 'utf8' })
    .split('\0').filter(Boolean).map(line => {
      const [, mode, type, sha, path] = /^(\d+) (\w+) ([0-9a-f]{40})\t(.*)$/s.exec(line);
      return { mode, type, sha, path };
    });
}

function record(component, target, destination) {
  if (!TARGETS.includes(target) || !/^[1-9][0-9]*$/.test(process.env.GITHUB_RUN_ID || '')) {
    throw new Error('An Actions run and a supported native target are required');
  }
  fs.writeFileSync(destination, JSON.stringify({
    schema: 1, component, target, run_id: process.env.GITHUB_RUN_ID,
    source_commit: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
    source_fingerprint: fingerprint(component, localTree()),
  }, null, 2) + '\n');
}

function versionFor(sha) {
  if (!/^[0-9a-f]{40}$/.test(sha)) throw new Error('Expected a full packaging commit');
  return `v0.1.0-dev.${sha.slice(0, 12)}`;
}

function inventory(version) {
  return ['aarch64', 'riscv64'].flatMap(arch => [
    `native-rust-${arch}-${version}.tar.zst`,
    `native-rust-${arch}-${version}.tar.zst.sha256`,
    `native-rust-${arch}-${version}.manifest.json`,
    `manifest-native-rust-${arch}.toml`,
  ]);
}

async function findRelease(github, repo, version) {
  try {
    return (await github.rest.repos.getReleaseByTag({ ...repo, tag: version })).data;
  } catch (error) {
    if (error.status !== 404) throw error;
    // The tag endpoint only exposes published releases. Authenticated release
    // listings also contain drafts, including one left by an interrupted upload.
    const releases = await github.paginate(github.rest.repos.listReleases, { ...repo, per_page: 100 });
    return releases.find(release => release.tag_name === version) || null;
  }
}

function validateRelease(release, sha, complete = !release?.draft) {
  if (!release) throw new Error('Native release could not be found');
  const version = versionFor(sha);
  if (release.tag_name !== version || release.target_commitish !== sha || !release.prerelease) {
    throw new Error('Existing native release belongs to different inputs');
  }
  if (complete) {
    const names = release.assets.filter(asset => asset.size > 0 && asset.state === 'uploaded')
      .map(asset => asset.name).sort();
    if (JSON.stringify(names) !== JSON.stringify(inventory(version).sort())) {
      throw new Error('Published native release is incomplete; refusing to replace it');
    }
  }
}

async function validateTag(github, repo, sha, required = true) {
  let ref;
  try {
    ref = (await github.rest.git.getRef({ ...repo, ref: `tags/${versionFor(sha)}` })).data.object;
  } catch (error) {
    if (error.status === 404 && !required) return;
    throw error;
  }
  for (let depth = 0; ref.type === 'tag' && depth < 4; depth++) {
    ref = (await github.rest.git.getTag({ ...repo, tag_sha: ref.sha })).data.object;
  }
  if (ref.type !== 'commit' || ref.sha !== sha) throw new Error('Native release tag points to a different commit');
}

async function isCurrent(github, context) {
  if (context.ref !== 'refs/heads/main' || context.eventName === 'pull_request') {
    throw new Error('Only main may publish a native toolchain');
  }
  const { data } = await github.rest.repos.getBranch({ ...context.repo, branch: 'main' });
  return data.commit.sha === context.sha;
}

function downloadRecord(repository, run, name) {
  const directory = fs.mkdtempSync(path.join(process.env.RUNNER_TEMP || os.tmpdir(), 'native-inputs-'));
  try {
    execFileSync('gh', ['run', 'download', String(run.id), '--repo', repository,
      '--name', name, '--dir', directory], { stdio: 'pipe' });
    return JSON.parse(fs.readFileSync(path.join(directory, 'native-inputs.json'), 'utf8'));
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

async function reusableRuns({ github, context, tree, download = downloadRecord,
  retry = Number(process.env.GITHUB_RUN_ATTEMPT || 1) > 1 }) {
  const repo = context.repo;
  const repository = `${repo.owner}/${repo.repo}`;
  const expected = Object.fromEntries(Object.keys(INPUTS).map(key => [key, fingerprint(key, tree, true)]));
  const chosen = { aarch64_run: '', riscv64_run: '', linker_run: '' };
  const { data } = await github.rest.actions.listWorkflowRunsForRepo({
    ...repo, status: 'success', per_page: 100,
  });
  // A previous attempt may have finished native compilation before packaging
  // or publication failed. Its successful component uploads remain reusable.
  const runs = [...data.workflow_runs];
  if (retry) runs.unshift({ id: context.runId, conclusion: 'success',
    head_repository: { full_name: repository }, path: '.github/workflows/build.yml',
    head_sha: context.sha, event: context.eventName });
  const sources = new Map();
  for (const run of runs) {
    if ((!retry && String(run.id) === String(context.runId)) || run.conclusion !== 'success'
        || run.head_repository?.full_name !== repository
        || !['.github/workflows/build.yml', '.github/workflows/native-host.yml',
          '.github/workflows/native-linker.yml'].includes(run.path)) continue;
    const artifacts = await github.paginate(github.rest.actions.listWorkflowRunArtifacts,
      { ...repo, run_id: run.id, per_page: 100 });
    const available = new Set(artifacts.filter(item => !item.expired).map(item => item.name));
    const matches = async (component, target) => {
      const name = `native-${component}-inputs-${target}`;
      if (!available.has(name) || !available.has(`native-${component}-${target}`)) return false;
      const record = await download(repository, run, name);
      if (record.schema !== 1 || record.component !== component || record.target !== target
          || String(record.run_id) !== String(run.id)
          || !/^[0-9a-f]{64}$/.test(record.source_fingerprint || '')
          || !/^[0-9a-f]{40}$/.test(record.source_commit || '')) return false;
      if (!sources.has(record.source_commit)) {
        const { data: commit } = await github.rest.git.getCommit({ ...repo, commit_sha: record.source_commit });
        const { data: source } = await github.rest.git.getTree({ ...repo, tree_sha: commit.tree.sha, recursive: '1' });
        if (source.truncated) throw new Error('Cannot validate a truncated native source tree');
        sources.set(record.source_commit, { commit, tree: source.tree });
      }
      const source = sources.get(record.source_commit);
      const runMatches = record.source_commit === run.head_sha || (run.event === 'pull_request'
        && source.commit.parents.length === 2 && source.commit.parents.some(parent => parent.sha === run.head_sha));
      // Validate the original complete record before comparing actual compiler
      // inputs. Publication-only edits must not invalidate already-built binaries.
      return runMatches && fingerprint(component, source.tree) === record.source_fingerprint
        && fingerprint(component, source.tree, true) === expected[component];
    };
    for (const [index, key] of ['aarch64_run', 'riscv64_run'].entries()) {
      if (!chosen[key] && await matches('host', TARGETS[index])) chosen[key] = String(run.id);
    }
    if (!chosen.linker_run && await matches('linker', TARGETS[0]) && await matches('linker', TARGETS[1])) {
      chosen.linker_run = String(run.id);
    }
    if (Object.values(chosen).every(Boolean)) break;
  }
  return chosen;
}

async function plan({ github, context, core }) {
  const version = versionFor(context.sha);
  core.setOutput('version', version);
  if (!await isCurrent(github, context)) {
    core.setOutput('skip', 'true');
    core.info('A newer main revision will publish the native toolchain');
    return;
  }
  core.setOutput('skip', 'false');
  const release = await findRelease(github, context.repo, version);
  if (release) validateRelease(release, context.sha);
  const published = Boolean(release && !release.draft);
  core.setOutput('published', String(published));
  const { data: scarlet } = await github.rest.repos.getBranch({ owner: 'petitstrawberry', repo: 'Scarlet', branch: 'dev' });
  core.setOutput('scarlet_commit', scarlet.commit.sha);
  if (!published) {
    const runs = await reusableRuns({ github, context, tree: localTree() });
    for (const [key, value] of Object.entries(runs)) {
      core.setOutput(key, value);
      core.info(`${key}: ${value || 'build after the checked cross toolchain'}`);
    }
  }
}

async function publish({ github, context, core, directory = 'release-assets', execute = execFileSync }) {
  if (!await isCurrent(github, context)) {
    core.setOutput('published', 'false');
    core.info('Skipped publication because main moved during the build');
    return;
  }
  const version = versionFor(context.sha);
  const repository = `${context.repo.owner}/${context.repo.repo}`;
  let release = await findRelease(github, context.repo, version);
  if (release) validateRelease(release, context.sha);
  await validateTag(github, context.repo, context.sha, Boolean(release && !release.draft));
  if (!release || release.draft) {
    const files = inventory(version).map(name => path.join(directory, name));
    for (const file of files) if (!fs.statSync(file).isFile()) throw new Error(`Missing release file: ${file}`);
    if (!release) {
      execute('gh', ['release', 'create', version, '--repo', repository, '--target', context.sha,
        '--draft', '--prerelease', '--title', `Scarlet native Rust ${version}`,
        '--notes', `Built from ${context.sha}. Includes AArch64 and RV64 rustc, Cranelift, static std and Wild. Guest execution is not verified by this build.`], { stdio: 'inherit' });
    }
    execute('gh', ['release', 'upload', version, '--repo', repository, '--clobber', ...files], { stdio: 'inherit' });
    validateRelease(await findRelease(github, context.repo, version), context.sha, true);
    // A release becomes visible only after both architectures have uploaded.
    if (!await isCurrent(github, context)) {
      core.setOutput('published', 'false');
      core.info('Left the superseded candidate as a draft');
      return;
    }
    execute('gh', ['release', 'edit', version, '--repo', repository, '--draft=false', '--prerelease'], { stdio: 'inherit' });
    release = await findRelease(github, context.repo, version);
    validateRelease(release, context.sha);
    await validateTag(github, context.repo, context.sha);
  }
  core.setOutput('published', 'true');
}

if (require.main === module) {
  const [command, component, target, destination] = process.argv.slice(2);
  if (command !== 'record' || !destination) throw new Error('Usage: native-artifacts.cjs record host|linker TARGET OUTPUT');
  record(component, target, destination);
}

module.exports = { fingerprint, localTree, versionFor, inventory, findRelease, validateRelease, validateTag,
  isCurrent, reusableRuns, plan, publish };
