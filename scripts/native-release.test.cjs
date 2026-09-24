const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const artifacts = require('./native-artifacts.cjs');
const update = require('./update-native-bundle.cjs');

const SHA = 'a'.repeat(40), RUST = 'b'.repeat(40), SCARLET = 'c'.repeat(40);
const VERSION = artifacts.versionFor(SHA);
const CONTEXT = { repo: { owner: 'petitstrawberry', repo: 'scarlet-rust-nix' },
  sha: SHA, ref: 'refs/heads/main', eventName: 'push', runId: 99 };
const TARGETS = ['aarch64-unknown-scarlet', 'riscv64gc-unknown-scarlet'];
const TREE = ['flake.nix', 'flake.lock', 'nix/build-toolchain.nix', 'native-host/recipe.json',
  'native-linker/recipe.json', 'scripts/native-artifacts.cjs', '.github/workflows/build.yml']
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

test('publication uploads both packages before making the draft visible', async t => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'native-publish-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  for (const name of artifacts.inventory(VERSION)) fs.writeFileSync(path.join(directory, name), 'fixture');
  let current = null;
  const commands = [];
  const github = { rest: {
    repos: {
      getBranch: async () => ({ data: { commit: { sha: SHA } } }),
      getReleaseByTag: async () => { if (!current) throw Object.assign(new Error('missing'), { status: 404 }); return { data: current }; },
    },
    git: { getRef: async () => {
      if (!current || current.draft) throw Object.assign(new Error('no tag'), { status: 404 });
      return { data: { object: { type: 'commit', sha: SHA } } };
    } },
  } };
  const log = core();
  await artifacts.publish({ github, context: CONTEXT, core: log, directory, execute: (program, args) => {
    assert.equal(program, 'gh'); commands.push(args[1]);
    if (args[1] === 'create') current = { ...release(true), assets: [] };
    if (args[1] === 'upload') {
      assert.equal(args.slice(-8).length, 8);
      assert.equal(current.draft, true); current.assets = release().assets;
    }
    if (args[1] === 'edit') { assert.equal(current.assets.length, 8); current.draft = false; }
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

const OLD = 'v0.1.0-rc.1';
const HASHES = { aarch64: '1'.repeat(64), riscv64: '2'.repeat(64) };
const BUNDLE = `# Keep this comment and the independent copy layer.\n[[layers]]\nkind = "archive"\nurl = "https://github.com/petitstrawberry/scarlet-rust-nix/releases/download/${OLD}/native-rust-{arch}-${OLD}.tar.zst"\nsha256 = { aarch64 = "sha256:${'0'.repeat(64)}", riscv64 = "sha256:${'0'.repeat(64)}" }\nformat = "tar-zst"\nstrip_components = 1\nto = "/opt/scarlet/toolchains/rust/${OLD}"\n\n[[layers]]\nkind = "copy"\nsource = "fs"\nto = "/"\n`;

function bundleFixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'native-bundle-test-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  for (const [index, arch] of ['aarch64', 'riscv64'].entries()) {
    const stem = `native-rust-${arch}-${VERSION}`;
    fs.writeFileSync(path.join(directory, `${stem}.manifest.json`), JSON.stringify({
      schema: 1, kind: 'scarlet-native-rust-toolchain', version: VERSION, packaging_commit: SHA,
      architecture: arch, target: TARGETS[index], install_prefix: `/opt/scarlet/toolchains/rust/${VERSION}`,
      rust: { revision: RUST, backend: 'cranelift' },
      requires: { scarlet_commit: SCARLET, dynamic_loader: '/bin/scarlet-ld' },
      components: { scarlet_ld: { included: false } },
      capabilities: { rustc: true, codegen: true, linker: true, static_target_std: true },
    }));
    fs.writeFileSync(path.join(directory, `${stem}.tar.zst.sha256`), `${HASHES[arch]}  ${stem}.tar.zst\n`);
  }
  const state = { main: SHA, head: SCARLET, manifest: BUNDLE, selected: OLD,
    order: 'ahead', compatible: 'ahead', updates: [], trees: [], commits: [], failOnce: false, release: release() };
  const github = { rest: {
    repos: {
      getReleaseByTag: async () => ({ data: state.release }),
      getBranch: async () => ({ data: { commit: { sha: state.main } } }),
      compareCommits: async args => ({ data: { status: args.repo === 'Scarlet' ? state.compatible : state.order } }),
    },
    git: {
      getRef: async args => ({ data: { object: { type: 'commit', sha: args.ref.startsWith('tags/') ? SHA : state.head } } }),
      getCommit: async args => ({ data: { tree: { sha: `tree-${args.commit_sha}` } } }),
      getTree: async () => ({ data: { truncated: false, tree: [
        { path: 'bundles/rust-toolchain/bundle.toml', sha: 'manifest', mode: '100644' },
        { path: 'bundles/rust-toolchain/fs/opt/scarlet/toolchains/rust/current', sha: 'current', mode: '120000' },
      ] } }),
      getBlob: async args => ({ data: { encoding: 'base64', content: Buffer.from(args.file_sha === 'manifest' ? state.manifest : state.selected).toString('base64') } }),
      createTree: async args => { state.trees.push(args); return { data: { sha: 'next-tree' } }; },
      createCommit: async args => { state.commits.push(args); return { data: { sha: `next-${state.commits.length}` } }; },
      updateRef: async args => {
        state.updates.push(args);
        if (state.failOnce) { state.failOnce = false; state.head = RUST; throw Object.assign(new Error('Not a fast forward'), { status: 422 }); }
      },
    },
  } };
  return { state, directory, github, execute: () => update({ github, context: CONTEXT, core: core(), directory, rustRevision: RUST }) };
}

test('bundle update commits both hashes, prefix and symlink together, preserving unrelated data', async t => {
  const f = bundleFixture(t); await f.execute();
  assert.equal(f.state.trees.length, 1);
  const tree = f.state.trees[0];
  assert.equal(tree.base_tree, `tree-${SCARLET}`);
  assert.equal(tree.tree.length, 2);
  assert.equal(tree.tree[1].mode, '120000');
  assert.equal(tree.tree[1].content, VERSION);
  const content = tree.tree[0].content;
  assert.match(content, /# Keep this comment/);
  assert.ok(content.endsWith('kind = "copy"\nsource = "fs"\nto = "/"\n'));
  assert.ok(content.includes(HASHES.aarch64) && content.includes(HASHES.riscv64));
  assert.ok(!content.includes(OLD));
  assert.deepEqual(f.state.commits[0].parents, [SCARLET]);
  assert.equal(f.state.updates[0].force, false);
  assert.equal(f.state.updates[0].ref, 'heads/dev');
});

test('concurrent dev changes are preserved by rebuilding the commit from the new parent', async t => {
  const f = bundleFixture(t); f.state.failOnce = true; await f.execute();
  assert.equal(f.state.updates.length, 2);
  assert.deepEqual(f.state.commits[1].parents, [RUST]);
  assert.equal(f.state.trees[1].base_tree, `tree-${RUST}`);
  assert.ok(f.state.updates.every(item => item.force === false));
});

test('a retried release is a no-op when the bundle already selects its exact checksums', async t => {
  const f = bundleFixture(t);
  f.state.manifest = update.updateManifest(BUNDLE, VERSION, HASHES).text;
  f.state.selected = VERSION;
  await f.execute();
  assert.equal(f.state.updates.length, 0); assert.equal(f.state.trees.length, 0);
});

test('stale runs do not change Scarlet', async t => {
  const f = bundleFixture(t); f.state.main = RUST; await f.execute();
  assert.equal(f.state.updates.length, 0);
});

test('wrong architecture, checksum name or Rust revision blocks updates before writing', async t => {
  for (const change of [
    f => { const file = path.join(f.directory, `native-rust-riscv64-${VERSION}.manifest.json`); const m = JSON.parse(fs.readFileSync(file)); m.target = TARGETS[0]; fs.writeFileSync(file, JSON.stringify(m)); },
    f => fs.writeFileSync(path.join(f.directory, `native-rust-riscv64-${VERSION}.tar.zst.sha256`), `${HASHES.riscv64}  wrong.tar.zst\n`),
    f => { const file = path.join(f.directory, `native-rust-aarch64-${VERSION}.manifest.json`); const m = JSON.parse(fs.readFileSync(file)); m.rust.revision = SHA; fs.writeFileSync(file, JSON.stringify(m)); },
  ]) {
    const f = bundleFixture(t); change(f); await assert.rejects(f.execute(), /Invalid native release/);
    assert.equal(f.state.trees.length, 0);
  }
});

test('incompatible loader, incomplete release, hash mismatch and rollback are rejected', async t => {
  for (const [change, expected] of [
    [f => { f.state.compatible = 'behind'; }, /required runtime loader/],
    [f => { f.state.release.assets.pop(); }, /incomplete/],
    [f => { f.state.release.assets[0].digest = `sha256:${'0'.repeat(64)}`; }, /checksum differs/],
    [f => { const old = artifacts.versionFor(RUST); f.state.manifest = BUNDLE.replaceAll(OLD, old); f.state.selected = old; f.state.order = 'behind'; }, /roll back/],
  ]) {
    const f = bundleFixture(t); change(f); await assert.rejects(f.execute(), expected);
    assert.equal(f.state.trees.length, 0);
  }
});
