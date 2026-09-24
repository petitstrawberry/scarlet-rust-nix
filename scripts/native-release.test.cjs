const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const artifacts = require('./native-artifacts.cjs');

const SHA = 'a'.repeat(40), RUST = 'b'.repeat(40), SCARLET = 'c'.repeat(40);
const VERSION = artifacts.versionFor(SHA);
const CONTEXT = { repo: { owner: 'petitstrawberry', repo: 'scarlet-rust-nix' },
  sha: SHA, ref: 'refs/heads/main', eventName: 'push', runId: 99 };
const TARGETS = ['aarch64-unknown-scarlet', 'riscv64gc-unknown-scarlet'];
const TREE = ['flake.nix', 'flake.lock', 'nix/build-toolchain.nix', 'native-host/recipe.json',
  'native-linker/recipe.json', 'scripts/native-artifacts.cjs', '.github/workflows/build.yml', 'native-host/README.md', 'native-linker/README.md']
  .map(path => ({ path, mode: '100644', type: 'blob', sha: SHA }));
const core = () => ({ info() {}, outputs: {}, setOutput(key, value) { this.outputs[key] = value; } });

function release(draft = false) {
  return { tag_name: VERSION, target_commitish: SHA, prerelease: true, draft,
    assets: artifacts.inventory(VERSION).map(name => ({ name, state: 'uploaded', size: 100 })) };
}

test('build identity changes with real inputs, but not docs or Git tree ordering', () => {
  const changed = file => TREE.map(entry => entry.path === file ? { ...entry, sha: RUST } : entry);
  for (const component of ['host', 'linker']) {
    const initial = artifacts.fingerprint(component, TREE);
    assert.notEqual(initial, artifacts.fingerprint(component, changed('flake.nix')));
    assert.notEqual(initial, artifacts.fingerprint(component, changed('nix/build-toolchain.nix')));
    assert.equal(initial, artifacts.fingerprint(component, [...TREE].reverse()));
    assert.equal(initial, artifacts.fingerprint(component, [...TREE, { ...TREE[0], path: 'README.md' }]));
  }
  assert.equal(artifacts.fingerprint('host', TREE), artifacts.fingerprint('host', changed('native-linker/recipe.json')));
  assert.notEqual(artifacts.fingerprint('linker', TREE), artifacts.fingerprint('linker', changed('native-linker/recipe.json')));
});

function reuseFixture() {
  const run = { id: 42, conclusion: 'success', event: 'pull_request', head_sha: RUST,
    head_repository: { full_name: 'petitstrawberry/scarlet-rust-nix' }, path: '.github/workflows/build.yml' };
  const list = [], records = {};
  for (const component of ['host', 'linker']) for (const target of TARGETS) {
    const name = `native-${component}-inputs-${target}`;
    list.push({ name, expired: false }, { name: `native-${component}-${target}`, expired: false });
    records[name] = { schema: 1, component, target, run_id: '42', source_commit: SHA,
      source_fingerprint: artifacts.fingerprint(component, TREE) };
  }
  const source = { tree: structuredClone(TREE), truncated: false };
  const commit = { tree: { sha: SHA }, parents: [{ sha: SCARLET }, { sha: RUST }] };
  const github = { rest: {
    actions: { listWorkflowRunsForRepo: async () => ({ data: { workflow_runs: [run] } }), listWorkflowRunArtifacts() {} },
    git: { getCommit: async () => ({ data: commit }), getTree: async () => ({ data: source }) },
  }, paginate: async () => list };
  return { run, list, records, source, commit, github,
    execute: (options = {}) => artifacts.reusableRuns({ github, context: CONTEXT, tree: TREE,
      download: async (_, __, name) => records[name], ...options }) };
}

test('reuses both architectures from a successful PR merge even though the main SHA differs', async () => {
  assert.deepEqual(await reuseFixture().execute(), { aarch64_run: '42', riscv64_run: '42', linker_run: '42' });
});

test('expired or missing native payloads only rebuild the affected component', async () => {
  const f = reuseFixture();
  f.list.find(item => item.name === `native-host-${TARGETS[1]}`).expired = true;
  f.list.splice(f.list.findIndex(item => item.name === `native-linker-${TARGETS[0]}`), 1);
  assert.deepEqual(await f.execute(), { aarch64_run: '42', riscv64_run: '', linker_run: '' });
});

test('does not trust copied provenance when the actual Git source differs', async () => {
  const f = reuseFixture();
  f.source.tree[0].sha = SCARLET;
  assert.deepEqual(await f.execute(), { aarch64_run: '', riscv64_run: '', linker_run: '' });
});

test('rejects a wrong run, unrelated source commit, failed run and external repository', async () => {
  for (const change of [
    f => Object.values(f.records).forEach(record => { record.run_id = '41'; }),
    f => { f.commit.parents = [{ sha: SCARLET }]; },
    f => { f.run.conclusion = 'failure'; },
    f => { f.run.head_repository.full_name = 'someone/fork'; },
  ]) {
    const f = reuseFixture(); change(f);
    assert.deepEqual(await f.execute(), { aarch64_run: '', riscv64_run: '', linker_run: '' });
  }
});

test('a host recipe change does not discard matching linker artifacts', async () => {
  const f = reuseFixture();
  for (const target of TARGETS) f.records[`native-host-inputs-${target}`].source_fingerprint = '0'.repeat(64);
  assert.deepEqual(await f.execute(), { aarch64_run: '', riscv64_run: '', linker_run: '42' });
});

test('publication-only edits reuse native binaries after verifying their original provenance', async () => {
  const f = reuseFixture();
  const tree = TREE.map(entry => ['scripts/native-artifacts.cjs', '.github/workflows/build.yml', 'native-host/README.md', 'native-linker/README.md'].includes(entry.path)
    ? { ...entry, sha: RUST } : entry);
  assert.deepEqual(await f.execute({ tree }), { aarch64_run: '42', riscv64_run: '42', linker_run: '42' });
  const changedCompiler = tree.map(entry => entry.path === 'native-host/recipe.json' ? { ...entry, sha: RUST } : entry);
  assert.deepEqual(await f.execute({ tree: changedCompiler }), { aarch64_run: '', riscv64_run: '', linker_run: '42' });
});

test('a retry reuses successful components from its own earlier failed attempt', async () => {
  const f = reuseFixture();
  f.run.conclusion = 'failure';
  for (const record of Object.values(f.records)) record.run_id = String(CONTEXT.runId);
  assert.deepEqual(await f.execute({ retry: true }), { aarch64_run: '99', riscv64_run: '99', linker_run: '99' });
  assert.deepEqual(await f.execute({ retry: false }), { aarch64_run: '', riscv64_run: '', linker_run: '' });
});

test('unverifiable or truncated source trees never permit reuse', async () => {
  const f = reuseFixture(); f.source.truncated = true;
  await assert.rejects(f.execute(), /truncated/);
});

test('published releases must contain both complete packages and match the packaging SHA', () => {
  artifacts.validateRelease(release(), SHA);
  const missing = release(); missing.assets.pop();
  assert.throws(() => artifacts.validateRelease(missing, SHA), /incomplete/);
  assert.throws(() => artifacts.validateRelease({ ...release(), target_commitish: RUST }, SHA), /different inputs/);
  assert.equal(artifacts.versionFor(SHA), VERSION);
});

test('finds a draft through the release listing when the published-tag endpoint returns 404', async () => {
  const draft = release(true);
  const github = {
    rest: { repos: {
      getReleaseByTag: async () => { throw Object.assign(new Error('Not Found'), { status: 404 }); },
      listReleases() {},
    } },
    paginate: async () => [{ ...draft, tag_name: 'v-unrelated' }, draft],
  };
  assert.equal(await artifacts.findRelease(github, CONTEXT.repo, VERSION), draft);
  github.paginate = async () => [];
  assert.equal(await artifacts.findRelease(github, CONTEXT.repo, VERSION), null);
  github.rest.repos.getReleaseByTag = async () => { throw Object.assign(new Error('Forbidden'), { status: 403 }); };
  github.paginate = async () => assert.fail('Authentication errors must not be hidden');
  await assert.rejects(artifacts.findRelease(github, CONTEXT.repo, VERSION), /Forbidden/);
});

test('publication uploads both packages before making the draft visible', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'native-publish-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  for (const name of artifacts.inventory(VERSION)) fs.writeFileSync(path.join(directory, name), 'fixture');
  let current = null;
  const commands = [];
  const github = { rest: {
    repos: {
      getBranch: async () => ({ data: { commit: { sha: SHA } } }),
      getReleaseByTag: async () => { if (!current || current.draft) throw Object.assign(new Error('missing'), { status: 404 }); return { data: current }; },
      listReleases() {},
    },
    git: { getRef: async () => {
      if (!current || current.draft) throw Object.assign(new Error('no tag'), { status: 404 });
      return { data: { object: { type: 'commit', sha: SHA } } };
    } },
  }, paginate: async () => current ? [current] : [] };
  const log = core();
  await artifacts.publish({ github, context: CONTEXT, core: log, directory, execute: (program, args) => {
    assert.equal(program, 'gh'); commands.push(args[1]);
    if (args[1] === 'create') current = { ...release(true), assets: [] };
    if (args[1] === 'upload') {
      assert.equal(args.slice(-10).length, 10);
      assert.equal(current.draft, true); current.assets = release().assets;
    }
    if (args[1] === 'edit') { assert.equal(current.assets.length, 10); current.draft = false; }
  } });
  assert.deepEqual(commands, ['create', 'upload', 'edit']);
  assert.equal(log.outputs.published, 'true');
  commands.length = 0;
  await artifacts.publish({ github, context: CONTEXT, core: log, directory, execute: () => commands.push('unexpected') });
  assert.deepEqual(commands, []);
});

test('superseded main runs cannot publish', async () => {
  const github = { rest: { repos: { getBranch: async () => ({ data: { commit: { sha: RUST } } }) } } };
  const log = core();
  await artifacts.publish({ github, context: CONTEXT, core: log, execute: () => assert.fail('must not publish') });
  assert.equal(log.outputs.published, 'false');
});

test('an existing release tag must resolve to the exact packaging commit', async () => {
  const github = { rest: { git: { getRef: async () => ({ data: { object: { type: 'commit', sha: RUST } } }) } } };
  await assert.rejects(artifacts.validateTag(github, CONTEXT.repo, SHA), /different commit/);
});
