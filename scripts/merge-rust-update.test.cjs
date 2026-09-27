const assert = require('node:assert/strict');
const { test } = require('node:test');
const merge = require('./merge-rust-update.cjs');

const oldRev = 'a'.repeat(40), newRev = 'b'.repeat(40);
const hash = `sha256-${'A'.repeat(43)}=`;
const vendor = ['x86_64-linux', 'aarch64-linux', 'aarch64-darwin']
  .map(system => `    ${system} = "${hash}";`).join('\n');
const before = {
  'flake.nix': `  rustRev = "${oldRev}";\n  rustHash = "${hash}";\n`,
  'nix/vendor-rust-src.nix': vendor,
};
const after = {
  ...before, 'flake.nix': before['flake.nix'].replace(oldRev, newRev),
};
const files = [{ filename: 'flake.nix', status: 'modified' }];

test('accepts only the generated revision and hash update', () => {
  assert.equal(merge.validateUpdate(files, before, after), newRev);
});

test('rejects unrelated paths, deleted files and Nix code changes', () => {
  for (const changes of [
    [...files, { filename: '.github/workflows/build.yml', status: 'modified' }],
    [{ filename: 'flake.nix', status: 'renamed' }],
  ]) assert.throws(() => merge.validateUpdate(changes, before, after));
  assert.throws(() => merge.validateUpdate(files, before, {
    ...after, 'flake.nix': after['flake.nix'] + '  enabled = true;\n',
  }));
});

test('accepts source and vendor hash refreshes at the same Rust revision', () => {
  const newHash = `sha256-${'B'.repeat(43)}=`;
  for (const path of Object.keys(before)) {
    const refreshed = { ...before, [path]: before[path].replaceAll(hash, newHash) };
    assert.equal(merge.validateUpdate([{ filename: path, status: 'modified' }], before, refreshed), oldRev);
  }
});

test('rejects malformed hashes, duplicate assignments and empty updates', () => {
  assert.throws(() => merge.validateUpdate([], before, before));
  assert.throws(() => merge.validateUpdate(files, before, before));
  assert.throws(() => merge.validateUpdate(files, before, {
    ...after, 'flake.nix': after['flake.nix'].replace(hash, 'invalid'),
  }));
  assert.throws(() => merge.validateUpdate(files, before, {
    ...after, 'flake.nix': after['flake.nix'] + `  rustRev = "${newRev}";\n`,
  }));
});

function fixture(updated = after, changedFiles = files) {
  const calls = [];
  const pr = {
    number: 42, state: 'open', draft: false,
    base: { ref: 'main', sha: 'base' },
    head: { ref: 'automation/rust-update', sha: 'head', repo: { full_name: 'owner/repo' } },
  };
  const main = { commit: { sha: 'base' } };
  const upstream = { sha: newRev };
  const github = {
    paginate: async () => changedFiles,
    rest: {
      pulls: {
        get: async () => ({ data: pr }),
        listFiles() {},
        updateBranch: async args => calls.push(['update', args]),
        merge: async args => { calls.push(['merge', args]); return { data: { merged: true } }; },
      },
      repos: {
        getBranch: async () => ({ data: main }),
        getCommit: async () => ({ data: upstream }),
        getContent: async ({ path, ref }) => ({ data: {
          type: 'file', encoding: 'base64',
          content: Buffer.from((ref === 'base' ? before : updated)[path]).toString('base64'),
        } }),
      },
    },
  };
  return {
    pr, main, upstream, calls,
    args: {
      github, core: { info() {} },
      context: { repo: { owner: 'owner', repo: 'repo' }, payload: { pull_request: structuredClone(pr) } },
    },
  };
}

test('merges only the SHA that passed CI', async () => {
  const f = fixture();
  await merge(f.args);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0][0], 'merge');
  assert.equal(f.calls[0][1].sha, 'head');
});

test('merges a vendor hash refresh only while its Rust revision is current', async () => {
  const path = 'nix/vendor-rust-src.nix';
  const refreshed = { ...before, [path]: before[path].replaceAll(hash, `sha256-${'B'.repeat(43)}=`) };
  for (const current of [oldRev, newRev]) {
    const f = fixture(refreshed, [{ filename: path, status: 'modified' }]);
    f.upstream.sha = current;
    await merge(f.args);
    if (current === oldRev) {
      assert.equal(f.calls[0][0], 'merge');
      assert.equal(f.calls[0][1].sha, 'head');
    } else {
      assert.deepEqual(f.calls, []);
    }
  }
});

test('does not merge superseded, draft or closed PRs', async () => {
  for (const change of [pr => { pr.head.sha = 'new-head'; }, pr => { pr.draft = true; }, pr => { pr.state = 'closed'; }]) {
    const f = fixture();
    change(f.pr);
    await merge(f.args);
    assert.deepEqual(f.calls, []);
  }
});

test('does not merge older upstream revisions', async () => {
  const f = fixture();
  f.upstream.sha = 'c'.repeat(40);
  await merge(f.args);
  assert.deepEqual(f.calls, []);
});

test('refreshes an outdated base for fresh CI instead of merging', async () => {
  const f = fixture();
  f.main.commit.sha = 'new-base';
  await merge(f.args);
  assert.equal(f.calls[0][0], 'update');
  assert.equal(f.calls[0][1].expected_head_sha, 'head');
});

test('never merges a fork or a different update branch', async () => {
  for (const change of [
    pr => { pr.head.repo.full_name = 'someone/fork'; },
    pr => { pr.head.ref = 'feature/work'; },
  ]) {
    const f = fixture();
    change(f.args.context.payload.pull_request);
    await assert.rejects(merge(f.args));
    assert.deepEqual(f.calls, []);
  }
});
