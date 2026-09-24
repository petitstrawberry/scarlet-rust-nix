// Advance only the released native toolchain's bundle and symlink in Scarlet.
const fs = require('node:fs');
const path = require('node:path');
const { versionFor, findRelease, validateRelease, validateTag, isCurrent } = require('./native-artifacts.cjs');

const CONSUMER = { owner: 'petitstrawberry', repo: 'Scarlet' };
const BUNDLE = 'bundles/rust-toolchain/bundle.toml';
const CURRENT = 'bundles/rust-toolchain/fs/opt/scarlet/toolchains/rust/current';
const PREFIX = '/opt/scarlet/toolchains/rust/';

function rustRevisionFromSource() {
  const revision = /^\s*rustRev = "([0-9a-f]{40})";/m.exec(fs.readFileSync('flake.nix', 'utf8'))?.[1];
  if (!revision) throw new Error('Missing pinned Rust revision');
  return revision;
}

function releaseInputs(directory, sha, rustRevision = rustRevisionFromSource()) {
  const version = versionFor(sha);
  const hashes = {};
  let scarletCommit;
  for (const [arch, target] of [['aarch64', 'aarch64-unknown-scarlet'], ['riscv64', 'riscv64gc-unknown-scarlet']]) {
    const stem = `native-rust-${arch}-${version}`;
    const manifest = JSON.parse(fs.readFileSync(path.join(directory, `${stem}.manifest.json`), 'utf8'));
    if (manifest.schema !== 1 || manifest.kind !== 'scarlet-native-rust-toolchain'
        || manifest.version !== version || manifest.packaging_commit !== sha
        || manifest.target !== target || manifest.architecture !== arch
        || manifest.install_prefix !== PREFIX + version || manifest.rust?.revision !== rustRevision
        || manifest.rust?.backend !== 'cranelift'
        || manifest.requires?.dynamic_loader !== '/bin/scarlet-ld'
        || manifest.components?.scarlet_ld?.included !== false
        || !/^[0-9a-f]{40}$/.test(manifest.requires?.scarlet_commit || '')
        || !['rustc', 'codegen', 'linker', 'static_target_std'].every(key => manifest.capabilities?.[key] === true)) {
      throw new Error(`Invalid native release manifest for ${arch}`);
    }
    if (scarletCommit && scarletCommit !== manifest.requires.scarlet_commit) {
      throw new Error('The two native packages require different Scarlet revisions');
    }
    scarletCommit = manifest.requires.scarlet_commit;
    const fields = fs.readFileSync(path.join(directory, `${stem}.tar.zst.sha256`), 'utf8').trim().split(/\s+/);
    if (fields.length !== 2 || !/^[0-9a-f]{64}$/.test(fields[0]) || fields[1] !== `${stem}.tar.zst`) {
      throw new Error(`Invalid native release checksum for ${arch}`);
    }
    hashes[arch] = fields[0];
  }
  return { version, hashes, scarletCommit };
}

function replaceOnce(text, pattern, replacement) {
  let matches = 0;
  const result = text.replace(pattern, (...args) => {
    matches++;
    return typeof replacement === 'function' ? replacement(...args) : replacement;
  });
  if (matches !== 1) throw new Error('Unexpected Scarlet native bundle layout');
  return result;
}

function updateManifest(text, version, hashes) {
  let previous;
  const root = 'https://github.com/petitstrawberry/scarlet-rust-nix/releases/download/';
  text = replaceOnce(text,
    /^url = "https:\/\/github\.com\/petitstrawberry\/scarlet-rust-nix\/releases\/download\/(v[0-9A-Za-z._+-]+)\/native-rust-\{arch\}-\1\.tar\.zst"$/gm,
    (_, old) => {
      previous = old;
      return `url = "${root}${version}/native-rust-{arch}-${version}.tar.zst"`;
    });
  text = replaceOnce(text, /^sha256 = \{[^\n]+\}$/gm,
    `sha256 = { aarch64 = "sha256:${hashes.aarch64}", riscv64 = "sha256:${hashes.riscv64}" }`);
  text = replaceOnce(text, /^to = "\/opt\/scarlet\/toolchains\/rust\/(v[0-9A-Za-z._+-]+)"$/gm,
    (_, old) => {
      if (old !== previous) throw new Error('Bundle URL and installation prefix disagree');
      return `to = "${PREFIX}${version}"`;
    });
  return { text, previous };
}

async function update({ github, context, core, directory = 'release-assets',
  rustRevision = rustRevisionFromSource() }) {
  const inputs = releaseInputs(directory, context.sha, rustRevision);
  const release = await findRelease(github, context.repo, inputs.version);
  if (!release || release.draft) throw new Error('The native release must be published before updating Scarlet');
  validateRelease(release, context.sha);
  await validateTag(github, context.repo, context.sha);
  for (const arch of ['aarch64', 'riscv64']) {
    const asset = release.assets.find(item => item.name === `native-rust-${arch}-${inputs.version}.tar.zst`);
    if (asset.digest && asset.digest !== `sha256:${inputs.hashes[arch]}`) {
      throw new Error(`Published archive checksum differs for ${arch}`);
    }
  }
  // Re-read after a non-fast-forward rejection; never replace another dev commit.
  for (let attempt = 0; attempt < 3; attempt++) {
    if (!await isCurrent(github, context)) {
      core.info('A newer toolchain revision owns the next Scarlet update');
      return;
    }
    const { data: ref } = await github.rest.git.getRef({ ...CONSUMER, ref: 'heads/dev' });
    const head = ref.object.sha;
    const { data: compatibility } = await github.rest.repos.compareCommits({
      ...CONSUMER, base: inputs.scarletCommit, head,
    });
    if (!['identical', 'ahead'].includes(compatibility.status)) {
      throw new Error('Scarlet dev does not contain the required runtime loader revision');
    }
    const { data: base } = await github.rest.git.getCommit({ ...CONSUMER, commit_sha: head });
    const { data: tree } = await github.rest.git.getTree({ ...CONSUMER, tree_sha: base.tree.sha, recursive: '1' });
    if (tree.truncated) throw new Error('Cannot update a truncated Scarlet source tree');
    const bundle = tree.tree.find(entry => entry.path === BUNDLE);
    const current = tree.tree.find(entry => entry.path === CURRENT);
    if (bundle?.mode !== '100644' || current?.mode !== '120000') {
      throw new Error('Expected a regular bundle manifest and a current symlink');
    }
    const read = async sha => {
      const { data } = await github.rest.git.getBlob({ ...CONSUMER, file_sha: sha });
      if (data.encoding !== 'base64') throw new Error('Unexpected Git blob encoding');
      return Buffer.from(data.content, 'base64').toString('utf8');
    };
    const before = await read(bundle.sha);
    const selected = await read(current.sha);
    const after = updateManifest(before, inputs.version, inputs.hashes);
    if (selected !== after.previous) throw new Error('Bundle and current symlink select different toolchains');
    if (after.previous === inputs.version) {
      if (after.text !== before) throw new Error('The selected immutable release has different checksums');
      core.info(`Scarlet already selects ${inputs.version}`);
      return;
    }
    const previousSha = /^v0\.1\.0-dev\.([0-9a-f]{12})$/.exec(after.previous)?.[1];
    if (previousSha) {
      const { data: order } = await github.rest.repos.compareCommits({
        ...context.repo, base: previousSha, head: context.sha,
      });
      if (!['ahead', 'identical'].includes(order.status)) throw new Error('Refusing to roll back the native toolchain');
    }
    const { data: nextTree } = await github.rest.git.createTree({
      ...CONSUMER, base_tree: base.tree.sha, tree: [
        { path: BUNDLE, mode: '100644', type: 'blob', content: after.text },
        { path: CURRENT, mode: '120000', type: 'blob', content: inputs.version },
      ],
    });
    const { data: commit } = await github.rest.git.createCommit({
      ...CONSUMER, message: `Update native Rust toolchain to ${inputs.version}`,
      tree: nextTree.sha, parents: [head],
      author: { name: 'github-actions[bot]', email: '41898282+github-actions[bot]@users.noreply.github.com' },
    });
    try {
      if (!await isCurrent(github, context)) {
        core.info('Skipped the Scarlet commit because main moved during the update');
        return;
      }
      await github.rest.git.updateRef({ ...CONSUMER, ref: 'heads/dev', sha: commit.sha, force: false });
      core.info(`Updated Scarlet bundle and symlink: ${commit.sha}`);
      return;
    } catch (error) {
      if (![409, 422].includes(error.status) || attempt === 2) throw error;
    }
  }
}

module.exports = update;
module.exports.releaseInputs = releaseInputs;
module.exports.updateManifest = updateManifest;
