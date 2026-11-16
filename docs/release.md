# v0.1.0 release freeze and publication

Historical v0.1.0 procedure: run its commands only from the owner-approved v0.1.0 source/merge or published tag. Current development is 0.2.0.dev0; do not build it under a v0.1.0 image/tag or expect its larger test suite/version to match this baseline's 188-test record.

Issue #14 owns this release. Source version 0.1.0 and its runtime pins are prepared through a feature PR; **the owner must approve/merge that version/changelog PR before tagging or publishing**. User authorization to freeze the release does not bypass the established owner-only merge policy. No tag/release is created by the preparation PR. Never force-move an existing release tag or overwrite a frozen baseline to add GUI features.

## Candidate verification — REL-UAT-01

From a clean candidate checkout on Linux/macOS with Python 3.12+ and Docker:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
python scripts/check_runtime_lock.py
python -m pip check
python scripts/check_test_catalogue.py
python -m unittest discover -s tests -v
calendar-sync --version
calendar-sync
```

Expected: 19 installed runtime pins and source/package version match; no broken requirements; 188 tests exactly catalogued and passing; version says Calendar Sync 0.1.0; bare command reports an offline check/no providers contacted. No credentials or provider access required. `SYNC_DRY_RUN=false` still cannot turn the bare default command into a worker; the explicit worker command is required.

PKG-001–004 cover valid lock/installed metadata, malformed/unpinned/duplicate/missing direct dependency entries and mismatch/missing-package fixed errors. PKG-004 executes the documented publication block with stub commands and verifies failure paths prevent publication. CFG-006 verifies the version command exits before settings or provider sessions. Existing safety-case IDs cover the functional baseline; the lock contains runtime versions, not all OS/build packages or hashes. Do not describe this as a full supply-chain audit.

## Container/artifact verification — REL-UAT-02

Use a committed candidate SHA; if there are local source modifications, commit them through the review workflow first rather than labeling them with a clean SHA:

```sh
export RELEASE_SOURCE_SHA="$(git rev-parse HEAD)"
docker build --build-arg VCS_REF="$RELEASE_SOURCE_SHA" -t calendar-sync:v0.1.0-candidate .
docker run --rm --network none --read-only calendar-sync:v0.1.0-candidate calendar-sync --version
docker run --rm --network none --read-only calendar-sync:v0.1.0-candidate python scripts/check_runtime_lock.py
docker run --rm --network none --read-only calendar-sync:v0.1.0-candidate
docker run --rm --network none --read-only --tmpfs /tmp:rw,mode=1777 \
  --mount "type=bind,src=$PWD/scripts,dst=/checks,readonly" \
  calendar-sync:v0.1.0-candidate python /checks/smoke_worker.py
docker image inspect calendar-sync:v0.1.0-candidate --format '{{.Id}} {{json .Config.Labels}}'
```

Expected version 0.1.0, 19 runtime pins, offline status, fake worker create/repeat/restart health PASS, OCI version 0.1.0 and revision matching the committed source. Record image ID, actual Python/architecture/dependency versions and source SHA privately in the release record. CI repeats these checks on Python 3.12/Linux and the Docker host architecture. A local ARM64 result alone is not AMD64 acceptance; the CI runtime must pass too. New image tags are local candidates only, not a published registry image or stable release tag.

All-container test reproduction mounts public tests/scripts/docs, no secrets or network:

```sh
docker run --rm --network none --read-only --tmpfs /tmp:rw,mode=1777 \
  --mount "type=bind,src=$PWD/tests,dst=/app/tests,readonly" \
  --mount "type=bind,src=$PWD/scripts,dst=/app/scripts,readonly" \
  --mount "type=bind,src=$PWD/docs,dst=/app/docs,readonly" \
  calendar-sync:v0.1.0-candidate python -m unittest discover -s tests -v
```

Expect 188 passes and automatic temporary-state cleanup. The base image remains a mutable upstream tag: retain the verified built artifact/image ID or publish and record an immutable digest through an explicitly authorized registry process. Do not invent a digest after a manifest-verification failure. GUI development must use another version/branch rather than rebuilding/replacing the retained customer image under the same release tag.

## Evidence and remaining acceptance — REL-UAT-03

Use [test-evidence.md](test-evidence.md) and [v0.1.0 notes](releases/v0.1.0.md). Record candidate/merge SHA and actual command results. Automated results do not replace missing visual/production checks in [reported worker evidence](worker-uat-evidence.md). Preserve explicit Not run/Not applicable reasons. Unsupported recurrence/deletes/GUI and other limitations must remain visible to customers. Real customer acceptance is governed by CUSTOMER-UAT-01–09, separate from source release publication.

The reviewer should confirm runtime pins/metadata/container labels agree; backup/recovery/secret handling still pass; all manual evidence is truthfully qualified; no new GUI/API dependencies/routes were included; default dry-run and no-delete behavior remain. Review [requirements.lock](../requirements.lock) as part of dependency maintenance. No license file is added by this release-preparation change.

## Owner merge, then immutable tag — REL-UAT-04

The following publication commands are **for after owner approval and merge of issue #14's release PR**, not commands run during candidate preparation. They create a tag/GitHub release; stop if the version tag already exists. Use an authenticated authorized publisher and a clean source checkout. Set a full reviewed merge SHA, never a moving main tip chosen without review:

```sh
git fetch origin --tags
export RELEASE_SHA='REPLACE_WITH_APPROVED_RELEASE_MERGE_SHA'
git cat-file -e "${RELEASE_SHA}^{commit}"
git merge-base --is-ancestor "$RELEASE_SHA" origin/main
git checkout --detach "$RELEASE_SHA"
```

Before publishing, rerun the candidate/version/lock/CI checks on that exact approved tree, verify required tests passed, and confirm GitHub has no existing v0.1.0 tag/release. Then the authorized publisher may create one annotated tag and publish the reviewed notes:

```sh
(
  set -eu
  expected="$(git rev-parse "${RELEASE_SHA:?Set the owner-approved release merge SHA}^{commit}")"
  test "$(git rev-parse HEAD)" = "$expected"
  git merge-base --is-ancestor "$expected" origin/main
  git diff --quiet "$expected" -- pyproject.toml requirements.lock src Dockerfile docs/releases/v0.1.0.md
  if git show-ref --tags --verify --quiet refs/tags/v0.1.0; then
    echo 'Local release tag already exists; stop and inspect' >&2
    exit 1
  fi
  remote_existing="$(git ls-remote --tags origin 'refs/tags/v0.1.0' 'refs/tags/v0.1.0^{}')"
  if test -n "$remote_existing"; then
    echo 'Remote release tag already exists; stop and inspect' >&2
    exit 1
  fi
  git tag -a v0.1.0 "$expected" -m 'Calendar Sync v0.1.0'
  git push origin refs/tags/v0.1.0
  remote_tag="$(git ls-remote --tags origin 'refs/tags/v0.1.0^{}')"
  remote_commit="${remote_tag%%[[:space:]]*}"
  test "$remote_commit" = "$expected"
  gh release create v0.1.0 --verify-tag --title 'Calendar Sync v0.1.0' --notes-file docs/releases/v0.1.0.md
)
```

The subshell fails immediately on tag/push/readback errors, and the remote annotated tag's peeled commit must equal the reviewed SHA before publication. An existing tag or rejected push is not permission to continue with --verify-tag alone. Do not use --force, delete/recreate a tag, silently substitute another commit, or create a release while the version PR is unmerged. These are publication actions; record them separately from test PASS. Keep the tag unchanged after publication; this procedure does not claim a server-side immutable-tag rule is configured. GitHub's built-in source archives follow the tag; no PyPI/registry package publication or image upload is performed by this sequence. If publishing extra wheel/image archives, build from the tagged approved tree, record SHA256 checksums and retain the exact artifacts; do not present unreviewed candidate builds as final assets.

For customer checkout after publication:

```sh
git fetch origin --tags
export CUSTOMER_SYNC_COMMIT="$(git rev-parse 'refs/tags/v0.1.0^{commit}')"
git checkout --detach "$CUSTOMER_SYNC_COMMIT"
```

Follow the production guide with that SHA. Future main/GUI commits do not automatically upgrade the customer. For rollback retain compatible pre-upgrade state/image; no in-place database downgrade or reset is introduced.

## GUI start and release boundary

The next delivery track is M4, beginning with [#17 API/owner authorization](https://github.com/salimsaidhemed/calendar-sync/issues/17), then [#19 Keycloak/configuration UI](https://github.com/salimsaidhemed/calendar-sync/issues/19), then [#18 audit/status UI](https://github.com/salimsaidhemed/calendar-sync/issues/18). Design may be prepared while this release PR is reviewed; production implementation branches must clearly track their baseline/dependencies. Do not call a GUI planned in an issue an implemented/tested feature.

The API must authorize every configuration/run/action by the validated Keycloak subject; never expose raw filesystem credential paths or refresh tokens to browser users. Existing operator-owned configurations need an explicit reviewed owner migration. The web process must not become a competing StateStore writer against the lifetime worker lock: specify a worker/service command boundary and scheduling strategy before enabling per-user editing/multiple configs. Require cross-user denial, session/token validation, expiry/CSRF and safe audit pagination acceptance before shipping those screens. These remain #17/#19/#18 work, not v0.1.0 capabilities.
