const BRANCH = 'automation/rust-update';
const FILES = ['flake.nix', 'nix/vendor-rust-src.nix'];
const SYSTEMS = ['x86_64-linux', 'aarch64-linux', 'aarch64-darwin'];

function replaceField(text, name, valuePattern, values) {
  const pattern = new RegExp(`^(\\s*${name} = ")(${valuePattern})(";)$`, 'gm');
  let count = 0;
  const result = text.replace(pattern, (_, before, value, after) => {
    count++;
    values[name] = value;
    return `${before}<updated>${after}`;
  });
  if (count !== 1) throw new Error(`Expected exactly one ${name} assignment`);
  return result;
}

function normalize(path, text) {
  const values = {};
  if (path === 'flake.nix') {
    text = replaceField(text, 'rustRev', '[0-9a-f]{40}', values);
    text = replaceField(text, 'rustHash', 'sha256-[A-Za-z0-9+/]{43}=', values);
  } else if (path === 'nix/vendor-rust-src.nix') {
    for (const system of SYSTEMS) {
      text = replaceField(text, system, 'sha256-[A-Za-z0-9+/]{43}=', values);
    }
  } else {
    throw new Error(`Unexpected update file: ${path}`);
  }
  return { text, values };
}

function validateUpdate(files, before, after) {
  if (!files.some(file => file.filename === 'flake.nix')) {
    throw new Error('The update must change flake.nix');
  }
  for (const file of files) {
    if (!FILES.includes(file.filename) || file.status !== 'modified') {
      throw new Error(`Unexpected update diff: ${file.filename} (${file.status})`);
    }
  }
  for (const path of FILES) {
    if (normalize(path, before[path]).text !== normalize(path, after[path]).text) {
      throw new Error(`The update changes more than revision/hash values in ${path}`);
    }
  }
  const oldRevision = normalize('flake.nix', before['flake.nix']).values.rustRev;
  const revision = normalize('flake.nix', after['flake.nix']).values.rustRev;
  if (revision === oldRevision) throw new Error('The Rust revision did not change');
  return revision;
}

async function merge({ github, context, core }) {
  const expected = context.payload.pull_request;
  const repo = context.repo;
  const fullName = `${repo.owner}/${repo.repo}`;
  if (!expected || expected.head.repo.full_name !== fullName || expected.head.ref !== BRANCH) {
    throw new Error('Only the same-repository Rust update branch can be merged');
  }
  const { data: pr } = await github.rest.pulls.get({ ...repo, pull_number: expected.number });
  if (pr.state !== 'open' || pr.draft || pr.head.sha !== expected.head.sha) {
    core.info('Skipping a closed, draft or superseded update');
    return;
  }
  if (pr.base.ref !== 'main' || pr.head.repo.full_name !== fullName || pr.head.ref !== BRANCH) {
    throw new Error('The pull request no longer has the expected branches');
  }
  const { data: main } = await github.rest.repos.getBranch({ ...repo, branch: 'main' });
  if (main.commit.sha !== expected.base.sha) {
    // Synchronize the branch and let the PAT trigger fresh CI against the new base.
    await github.rest.pulls.updateBranch({
      ...repo, pull_number: pr.number, expected_head_sha: pr.head.sha,
    });
    core.info('The base changed; updated the branch for a fresh CI run');
    return;
  }
  const files = await github.paginate(github.rest.pulls.listFiles, {
    ...repo, pull_number: pr.number, per_page: 100,
  });
  const read = async (path, ref) => {
    const { data } = await github.rest.repos.getContent({ ...repo, path, ref });
    if (data.type !== 'file' || data.encoding !== 'base64') throw new Error(`Invalid file: ${path}`);
    return Buffer.from(data.content, 'base64').toString('utf8');
  };
  const before = {}, after = {};
  for (const path of FILES) {
    before[path] = await read(path, expected.base.sha);
    after[path] = await read(path, pr.head.sha);
  }
  const revision = validateUpdate(files, before, after);
  const { data: upstream } = await github.rest.repos.getCommit({
    owner: 'petitstrawberry', repo: 'rust', ref: 'scarlet-target',
  });
  if (revision !== upstream.sha) {
    core.info('A newer Scarlet Rust revision exists; leaving this PR for the updater');
    return;
  }
  // All matrix builds and uploads have succeeded. Required status checks also
  // prevent merging if main moves after the base check above.
  const { data: result } = await github.rest.pulls.merge({
    ...repo, pull_number: pr.number, sha: pr.head.sha, merge_method: 'squash',
  });
  if (!result.merged) throw new Error(result.message);
  core.info(`Merged Rust ${revision}: ${result.sha}`);
}

module.exports = merge;
module.exports.validateUpdate = validateUpdate;
