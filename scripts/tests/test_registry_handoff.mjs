import assert from "node:assert/strict";
import {
  constants,
  createDecipheriv,
  createHash,
  generateKeyPairSync,
  privateDecrypt,
} from "node:crypto";
import test from "node:test";
import {
  encrypt,
  obtainPullToken,
  validateImages,
  validateRun,
} from "../registry_handoff.mjs";

const revision = "a".repeat(40);
const repository = "aoodie/Tauric";
const run = {
  conclusion: "success",
  status: "completed",
  head_sha: revision,
  path: ".github/workflows/release.yml",
  repository: { full_name: repository },
  head_repository: { full_name: repository },
};

test("only an exact successful same-repository release can delegate image reads", () => {
  validateRun(run, repository, revision);
  for (const change of [
    { conclusion: "failure" },
    { head_sha: "b".repeat(40) },
    { path: "other.yml" },
    { head_repository: { full_name: "other/repo" } },
  ]) {
    assert.throws(() =>
      validateRun({ ...run, ...change }, repository, revision),
    );
  }
  const entries = ["api", "worker", "web", "openclaw"].map((kind) => ({
    revision,
    repository: repository.toLowerCase(),
    image: `ghcr.io/aoodie/tauric-${kind}@sha256:${"b".repeat(64)}`,
  }));
  assert.equal(validateImages(entries, repository, revision).length, 4);
  assert.throws(() => validateImages(entries.slice(1), repository, revision));
  assert.throws(() =>
    validateImages(
      [
        {
          ...entries[0],
          image: entries[0].image.replace("tauric-api", "unrelated"),
        },
        ...entries.slice(1),
      ],
      repository,
      revision,
    ),
  );
});

test("encrypted pull credentials authenticate ciphertext and require the ephemeral recipient", () => {
  const { privateKey, publicKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
  });
  const recipient = publicKey.export({ type: "spki", format: "pem" });
  const payload = {
    revision,
    pulls: [
      {
        token: "private-pull-fixture",
        scope: "repository:aoodie/tauric-api:pull",
      },
    ],
  };
  const result = encrypt(payload, recipient);
  assert.ok(!JSON.stringify(result).includes("private-pull-fixture"));
  const key = privateDecrypt(
    {
      key: privateKey,
      padding: constants.RSA_PKCS1_OAEP_PADDING,
      oaepHash: "sha256",
    },
    Buffer.from(result.wrapped_key, "base64"),
  );
  const decrypt = (content) => {
    const cipher = createDecipheriv(
      "aes-256-gcm",
      key,
      Buffer.from(result.nonce, "base64"),
    );
    cipher.setAuthTag(Buffer.from(result.tag, "base64"));
    return Buffer.concat([cipher.update(content), cipher.final()]).toString();
  };
  const ciphertext = Buffer.from(result.ciphertext, "base64");
  assert.deepEqual(JSON.parse(decrypt(ciphertext)), payload);
  ciphertext[0] ^= 1;
  assert.throws(() => decrypt(ciphertext));
  const weak = generateKeyPairSync("rsa", {
    modulusLength: 1024,
  }).publicKey.export({ type: "spki", format: "pem" });
  assert.throws(() => encrypt(payload, weak));
});

test("authorization requests only pull scope and checks exact manifest bytes", async () => {
  const manifest = Buffer.from('{"schemaVersion":2,"manifests":[]}');
  const digest =
    "sha256:" + createHash("sha256").update(manifest).digest("hex");
  const image = "ghcr.io/aoodie/tauric-api@" + digest;
  const token = "fixture-pull-token-with-no-production-access";
  const requests = [];
  const request = async (url, options) => {
    requests.push({ url: String(url), options });
    if (requests.length === 1)
      return { ok: true, json: async () => ({ token }) };
    return { ok: true, arrayBuffer: async () => manifest };
  };
  const result = await obtainPullToken(
    image,
    "owner",
    "workflow-private-fixture",
    request,
  );
  assert.equal(
    new URL(requests[0].url).searchParams.get("scope"),
    "repository:aoodie/tauric-api:pull",
  );
  assert.ok(!requests.some((row) => row.url.includes("push")));
  assert.equal(requests[1].options.headers.Authorization, "Bearer " + token);
  assert.equal(requests[1].options.redirect, "error");
  assert.equal(result.image, image);
  assert.equal(result.scope, "repository:aoodie/tauric-api:pull");
  await assert.rejects(
    obtainPullToken(image, "owner", "fixture", async () => ({ ok: false })),
  );
});
