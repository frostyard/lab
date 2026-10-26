#!/bin/bash
# In-container half of the read-only Snow lifecycle preflight (see run.sh).
# Prints one line per check: "CHECK <name> <PASS|FAIL|PENDING|INFO> <detail>".
# /work holds manifest.json and the ConfigMap qa.py; /iso.iso is an optional
# local ISO copy to hash instead of a download. Nothing here writes to a
# registry or moves a tag. Exit 0 only when no check failed.
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
if ! { apt-get update -qq && apt-get install -y -qq --no-install-recommends \
      ca-certificates python3 curl gnupg gpgv skopeo cosign mtools zstd cpio jq binutils; } >/dev/null 2>&1; then
  echo "CHECK tools FAIL packages_unavailable"
  exit 2
fi
M=/work/manifest.json
T=$(mktemp -d)
trap 'rm -rf -- "$T"' EXIT
fails=0
check() { # name status detail
  printf 'CHECK %s %s %s\n' "$1" "$2" "$3"
  if [[ $2 == FAIL ]]; then fails=$((fails + 1)); fi
}
same() { # name got want pass-detail
  if [[ -n $2 && $2 == "$3" ]]; then check "$1" PASS "$4"; else check "$1" FAIL "got=${2:-none} want=$3"; fi
}
j() { jq -r "$1" "$M"; }
commit=$(j .snosi_commit)
base="https://raw.githubusercontent.com/frostyard/snosi/$commit"
iso_base="https://repository.frostyard.org/isos/native/v1"
echo "tools: $(cosign version 2>/dev/null | awk '/GitVersion/{print "cosign", $2}'); $(skopeo --version); $(gpgv --version | head -1)"
echo "manifest file sha256=$(sha256sum "$M" | cut -c1-64)"

# 1. Public trust material at the pinned snosi commit.
while read -r field path local; do
  if curl -fsSL --max-time 60 -o "$T/$local" "$base/$path"; then
    got=$(sha256sum "$T/$local" | cut -c1-64)
    same "$field" "$got" "$(j ".$field")" "$path@${commit:0:12} sha256=$got"
  else
    check "$field" FAIL "download $base/$path"
  fi
done <<'EOF'
index_key_sha256 shared/native-ab/keys/import-pubring.gpg index.gpg
cosign_key_sha256 cosign.pub cosign.pub
mok_cert_sha256 shared/native-ab/keys/mok-2026.crt mok.crt
EOF

# 2. Index key primary fingerprint.
fpr=$(gpg --show-keys --with-colons "$T/index.gpg" 2>/dev/null |
  awk -F: '$1 == "pub" {getline; if ($1 == "fpr") print $10}' | head -1)
same index_key_fingerprint "$fpr" "$(j .trust_fingerprint)" "$fpr"

# 3. Signed ISO index: signature by the pinned primary key, then the entry.
if curl -fsSL --max-time 60 -o "$T/SHA256SUMS" "$iso_base/SHA256SUMS" &&
   curl -fsSL --max-time 60 -o "$T/SHA256SUMS.gpg" "$iso_base/SHA256SUMS.gpg"; then
  # gpgv must succeed as a whole (one bad extra signature fails it), and the
  # only valid signer must be the pinned primary key.
  if gpgv --status-fd=1 --keyring "$T/index.gpg" "$T/SHA256SUMS.gpg" "$T/SHA256SUMS" > "$T/gpgv.status" 2>/dev/null; then
    signer=$(awk '$2 == "VALIDSIG" {print $NF}' "$T/gpgv.status")
    same iso_index_signature "$signer" "$(j .trust_fingerprint)" "gpgv ok, VALIDSIG primary=$signer"
  else
    check iso_index_signature FAIL "gpgv rejected SHA256SUMS.gpg"
  fi
  name=$(basename "$(j .iso_url)")
  entry=$(awk -v n="$name" '$2 == n || $2 == "*" n {print $1}' "$T/SHA256SUMS")
  same iso_index_entry "$entry" "$(j .iso_sha256)" "$name $entry ($(wc -l < "$T/SHA256SUMS") entries in the index)"
else
  check iso_index FAIL "cannot download $iso_base/SHA256SUMS{,.gpg}"
fi
same iso_url_versioned "$(j .iso_url)" "$iso_base/snosi-installer_$(j .iso_version)_x86-64.iso" "$(j .iso_url)"

# 4. ISO bytes (a local copy at /iso.iso, or a download).
iso=/iso.iso
if [[ ! -f $iso ]]; then
  iso="$T/iso.iso"
  curl -fsSL -o "$iso" "$(j .iso_url)" || check iso_download FAIL "$(j .iso_url)"
fi
got=$(sha256sum "$iso" 2>/dev/null | cut -c1-64)
same iso_sha256 "$got" "$(j .iso_sha256)" "$got ($(stat -c %s "$iso" 2>/dev/null) bytes)"

# 5. ISO contents: ESP partition 2, Firn initramfs, embedded keys, catalog, release.
if off=$(python3 - "$iso" <<'PY'
import struct, sys
with open(sys.argv[1], "rb") as f:
    f.seek(512); h = f.read(512)
    if h[:8] != b"EFI PART":
        sys.exit(1)
    lba, count, width = struct.unpack_from("<QII", h, 72)
    f.seek(lba * 512); e = f.read(count * width)
s = e[128:256]
if s[:16] != bytes.fromhex("28732ac11ff8d211ba4b00a0c93ec93b"):
    sys.exit(1)
print(struct.unpack_from("<Q", s, 32)[0] * 512)
PY
); then
  check iso_esp PASS "GPT partition 2 is an ESP at byte $off"
else
  check iso_esp FAIL "no ESP as GPT partition 2"
fi
if [[ -n ${off:-} ]] && mcopy -i "$iso@@$off" ::firn-installer/initrd.img "$T/initrd.img" 2>/dev/null &&
   zstd -dcq "$T/initrd.img" > "$T/initrd.cpio"; then
  check iso_initramfs PASS "firn-installer/initrd.img $(stat -c %s "$T/initrd.img") bytes"
  mkdir -p "$T/x"
  (cd "$T/x" && cpio -idm --quiet usr/bin/firn etc/firn/catalog.json usr/lib/snosi/cosign.pub \
     usr/lib/snosi/os-update-pubring.gpg usr/lib/snosi/mok.crt etc/snosi-installer-release < "$T/initrd.cpio" 2>/dev/null)
  while read -r member pinned; do
    if cmp -s "$T/x/$member" "$T/$pinned"; then
      check "iso_embedded:/$member" PASS "equals pinned $pinned"
    else
      check "iso_embedded:/$member" FAIL "differs from pinned $pinned"
    fi
  done <<'EOF'
usr/lib/snosi/cosign.pub cosign.pub
usr/lib/snosi/os-update-pubring.gpg index.gpg
usr/lib/snosi/mok.crt mok.crt
EOF
  snow=$(jq -c '[.[] | select(.family == "bootc" and .name == "snow")] | {n: length, ref: .[0].ref, key: .[0].cosign_pub_key}' \
    "$T/x/etc/firn/catalog.json" 2>/dev/null)
  same iso_catalog "$(jq -r '.n == 1 and .key == "/usr/lib/snosi/cosign.pub"' <<<"$snow" 2>/dev/null)" true "$snow"
  release=$(tr '\n' ' ' < "$T/x/etc/snosi-installer-release" 2>/dev/null)
  if [[ $release == *"$(j .iso_version)"* ]]; then check iso_release PASS "$release"; else check iso_release FAIL "$release"; fi
  firn_version=$(strings "$T/x/usr/bin/firn" 2>/dev/null | grep -m1 -E '^v?[0-9]+\.[0-9]+\.[0-9]+$')
  check iso_firn INFO "sha256=$(sha256sum "$T/x/usr/bin/firn" | cut -c1-64) version=${firn_version:-unknown}"
  # Save the listing first: grep -q closing a pipe early must not look like absence.
  if ! cpio -it < "$T/initrd.cpio" > "$T/members" 2>/dev/null; then
    check iso_bootc FAIL "cannot list the installer initramfs"
  elif grep -qE '(^|/)usr/bin/bootc$' "$T/members"; then
    check iso_bootc INFO "bootc is in the installer initramfs: Firn runs it natively"
  else
    check iso_bootc INFO "no bootc in the installer initramfs: Firn runs bootc from image N with podman"
  fi
else
  check iso_initramfs FAIL "cannot read firn-installer/initrd.img"
fi

# 6. OCI images: cosign signature, digest, labels, annotation, version tag.
for which in n n_plus_1; do
  ref=$(j ".image_$which"); ver=$(j ".version_$which"); tag=$(j ".version_tag_$which")
  digest=${ref#*@}
  if cosign verify --key "$T/cosign.pub" "$ref" > "$T/cosign-$which.json" 2>/dev/null; then
    claims=$(jq -s 'flatten | length' "$T/cosign-$which.json")
    signed=$(jq -rs 'flatten | map(.critical.image["docker-manifest-digest"]) | unique | join(",")' "$T/cosign-$which.json")
    same "oci_signature_$which" "$signed" "$digest" "$claims claim(s), signed digest=$signed"
  else
    check "oci_signature_$which" FAIL "cosign verify failed"
  fi
  info=$(skopeo inspect --no-tags "docker://$ref" 2>/dev/null)
  raw=$(skopeo inspect --raw "docker://$ref" 2>/dev/null)
  same "oci_digest_$which" "$(jq -r .Digest <<<"$info")" "$digest" "$digest"
  labels=$(jq -c '.Labels | {v: .["org.opencontainers.image.version"], a: .["io.snosi.bootc.secureboot-assembly"], c: .["io.snosi.bootc.secureboot-capable"]}' <<<"$info")
  same "oci_labels_$which" "$(jq -r --arg v "$ver" '.v == $v and .a == "bootc-1.16.8-storage-digest-v1" and .c == "true"' <<<"$labels")" true "$labels"
  ann=$(jq -r '.annotations["org.opencontainers.image.version"] // empty' <<<"$raw")
  same "oci_version_annotation_$which" "$ann" "$ver" "$ann ($(jq -r .mediaType <<<"$raw"))"
  tagd=$(skopeo inspect --no-tags "docker://$tag" 2>/dev/null | jq -r .Digest)
  same "version_tag_$which" "$tagd" "$digest" "$tag -> $tagd"
done
if [[ $(j .version_n) < $(j .version_n_plus_1) && $(j .image_n) != "$(j .image_n_plus_1)" ]]; then
  check order_distinct PASS "$(j .version_n) < $(j .version_n_plus_1), digests distinct"
else
  check order_distinct FAIL "versions not ordered or digests equal"
fi

# 7. Controlled target tag: an operator moves it; it must resolve to N+1 at submit.
target=$(j .target_ref)
next=$(j .image_n_plus_1); next=${next#*@}
target_ready=0
if td=$(skopeo inspect --no-tags "docker://$target" 2>/dev/null | jq -r .Digest); then
  if [[ $td == "$next" ]]; then
    check target_ref PASS "$target -> $td"; target_ready=1
  else
    check target_ref PENDING "$target -> $td (not N+1: move it before submit)"
  fi
else
  check target_ref PENDING "$target does not exist yet (create it before submit)"
fi

# 8. The lane's own gates: ConfigMap qa.py validate, then preflight. While the
# target tag is not ready, only the target-tag resolve is deferred.
if python3 /work/qa.py validate "$M" "$T/normalized.json" > "$T/hash" 2> "$T/err"; then
  check lane_validate PASS "canonical manifest_sha256=$(cat "$T/hash")"
  if (( target_ready )); then
    if python3 /work/qa.py preflight "$T/normalized.json" "$T/checks.json" 2> "$T/err"; then
      check lane_preflight PASS "full qa.py preflight"
    else
      check lane_preflight FAIL "$(head -c 60 "$T/err")"
    fi
  elif python3 - "$T/normalized.json" "$T/checks.json" 2> "$T/err" <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("qa", "/work/qa.py")
qa = importlib.util.module_from_spec(spec); spec.loader.exec_module(qa)
m = qa.load(sys.argv[1], frozen=True)
real = qa.resolve
def resolve(ref, digest):
    if ref != m["target_ref"]:
        real(ref, digest)
qa.resolve = resolve
try:
    qa.preflight(m, sys.argv[2])
except qa.GateError as exc:
    sys.exit("qa_failed:" + str(exc))
PY
  then
    check lane_preflight PASS "qa.py preflight with only the target-tag resolve deferred"
  else
    check lane_preflight FAIL "$(head -c 60 "$T/err")"
  fi
  if [[ -s $T/checks.json ]]; then echo "lane preflight.json: $(cat "$T/checks.json")"; fi
else
  check lane_validate FAIL "$(head -c 60 "$T/err")"
fi
echo "failures=$fails target_ready=$target_ready"
(( fails == 0 ))
