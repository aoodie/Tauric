/* Encrypt narrowly scoped registry access without exporting the GitHub token. */
import {
  constants,
  createCipheriv,
  createHash,
  createPublicKey,
  publicEncrypt,
  randomBytes,
} from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const kinds = ["api", "worker", "web", "openclaw"];
const requireFact = (condition, message) => {
  if (!condition) throw new Error(message);
};

export function validateRun(run, repository, revision) {
  requireFact(
    /^[a-f0-9]{40}$/.test(revision),
    "A full release revision is required.",
  );
  requireFact(
    /^[a-z0-9_.-]+\/[a-z0-9_.-]+$/i.test(repository),
    "Invalid repository.",
  );
  requireFact(
    run.conclusion === "success" &&
      run.status === "completed" &&
      run.head_sha === revision &&
      run.path === ".github/workflows/release.yml" &&
      run.repository?.full_name?.toLowerCase() === repository.toLowerCase() &&
      run.head_repository?.full_name?.toLowerCase() ===
        repository.toLowerCase(),
    "A successful same-repository release at the exact revision is required.",
  );
}

export function validateImages(entries, repository, revision) {
  requireFact(
    entries.length === kinds.length,
    "Exactly four release images are required.",
  );
  return entries.map((entry, index) => {
    const prefix = `ghcr.io/${repository.toLowerCase()}-${kinds[index]}@sha256:`;
    requireFact(
      entry.revision === revision &&
        entry.repository === repository.toLowerCase() &&
        entry.image?.startsWith(prefix) &&
        /^[a-f0-9]{64}$/.test(entry.image.slice(prefix.length)),
      "Published image identity differs from the successful release.",
    );
    return entry.image;
  });
}

export function encrypt(payload, recipient) {
  const publicKey = createPublicKey(recipient);
  requireFact(
    publicKey.asymmetricKeyType === "rsa" &&
      publicKey.asymmetricKeyDetails.modulusLength >= 2048,
    "Use an RSA recipient of at least 2048 bits.",
  );
  const key = randomBytes(32);
  const nonce = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", key, nonce);
  const ciphertext = Buffer.concat([
    cipher.update(JSON.stringify(payload)),
    cipher.final(),
  ]);
  const wrapped = publicEncrypt(
    {
      key: publicKey,
      padding: constants.RSA_PKCS1_OAEP_PADDING,
      oaepHash: "sha256",
    },
    key,
  );
  return {
    format: 1,
    algorithm: "RSA-OAEP-SHA256+AES-256-GCM",
    revision: payload.revision,
    wrapped_key: wrapped.toString("base64"),
    nonce: nonce.toString("base64"),
    tag: cipher.getAuthTag().toString("base64"),
    ciphertext: ciphertext.toString("base64"),
  };
}

export async function obtainPullToken(
  image,
  actor,
  credential,
  request = fetch,
) {
  const [repository, digest] = image.slice("ghcr.io/".length).split("@");
  const url = new URL("https://ghcr.io/token");
  url.searchParams.set("service", "ghcr.io");
  url.searchParams.set("scope", `repository:${repository}:pull`);
  const response = await request(url, {
    redirect: "error",
    headers: {
      Authorization:
        "Basic " + Buffer.from(`${actor}:${credential}`).toString("base64"),
    },
  });
  requireFact(response.ok, "Registry pull authorization failed.");
  const result = await response.json();
  requireFact(
    typeof result.token === "string" && result.token.length > 20,
    "Missing registry pull token.",
  );
  const manifest = await request(
    `https://ghcr.io/v2/${repository}/manifests/${digest}`,
    {
      redirect: "error",
      headers: {
        Authorization: "Bearer " + result.token,
        Accept:
          "application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json",
      },
    },
  );
  requireFact(manifest.ok, "Registry token cannot read the immutable image.");
  const body = Buffer.from(await manifest.arrayBuffer());
  requireFact(
    "sha256:" + createHash("sha256").update(body).digest("hex") === digest,
    "Downloaded manifest differs from the published digest.",
  );
  return {
    image,
    token: result.token,
    scope: `repository:${repository}:pull`,
    expires_in: result.expires_in ?? null,
  };
}

async function main() {
  const {
    GITHUB_REPOSITORY: repository,
    GITHUB_ACTOR: actor,
    GH_TOKEN: credential,
    SOURCE_REVISION: revision,
    SOURCE_RUN: runId,
    RECIPIENT_PUBLIC_KEY: recipient,
  } = process.env;
  requireFact(/^\d{1,20}$/.test(runId ?? ""), "A release run ID is required.");
  requireFact(
    credential && actor,
    "Workflow registry credentials are required.",
  );
  const response = await fetch(
    `https://api.github.com/repos/${repository}/actions/runs/${runId}`,
    {
      redirect: "error",
      headers: {
        Authorization: "Bearer " + credential,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
    },
  );
  requireFact(response.ok, "Release run could not be verified.");
  validateRun(await response.json(), repository, revision);
  const entries = await Promise.all(
    kinds.map(async (kind) =>
      JSON.parse(await readFile(`image-digests/${kind}.json`, "utf8")),
    ),
  );
  const images = validateImages(entries, repository, revision);
  const pulls = [];
  for (const image of images)
    pulls.push(await obtainPullToken(image, actor, credential));
  const encrypted = encrypt(
    { revision, created_at: new Date().toISOString(), pulls },
    recipient,
  );
  await mkdir("artifacts", { recursive: true });
  await writeFile(
    "artifacts/encrypted-registry-access.json",
    JSON.stringify(encrypted, null, 2) + "\n",
    { mode: 0o600, flag: "wx" },
  );
  console.log(
    "Encrypted four verified image-only pull tokens; GitHub credential remains inside the workflow.",
  );
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  main().catch(() => {
    console.error(
      "Encrypted registry handoff failed; no credentials were printed.",
    );
    process.exitCode = 1;
  });
}
