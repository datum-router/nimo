#!/bin/bash
# Build nimo-autofill.apk with plain SDK command-line tools (no Gradle).
#   ANDROID_SDK_ROOT=~/workspace/android-sdk ./build.sh
# Output: nimo-autofill.apk (signed with debug.keystore, committed to repo)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SDK="${ANDROID_SDK_ROOT:-$HOME/workspace/android-sdk}"
BT="$SDK/build-tools/34.0.0"
PLATFORM="$SDK/platforms/android-34"
OUT="$HERE/build"

for t in "$BT/aapt2" "$BT/d8" "$BT/apksigner" "$BT/zipalign" "$PLATFORM/android.jar"; do
    [ -e "$t" ] || { echo "missing: $t" >&2; exit 1; }
done

rm -rf "$OUT"
mkdir -p "$OUT/classes" "$OUT/dex" "$OUT/gen"

echo "[1/6] javac"
find "$HERE/src" -name '*.java' > "$OUT/sources.txt"
javac -source 8 -target 8 -nowarn -cp "$PLATFORM/android.jar" \
    -d "$OUT/classes" @"$OUT/sources.txt"

echo "[2/6] JVM unit tests (FieldClassifier, CredStore)"
javac -nowarn -cp "$OUT/classes" -d "$OUT/test-classes" "$HERE/test/TestNimo.java"
java -cp "$OUT/classes:$OUT/test-classes" TestNimo

echo "[3/6] d8"
"$BT/d8" --lib "$PLATFORM/android.jar" --min-api 26 \
    --output "$OUT/dex" $(find "$OUT/classes" -name '*.class')

echo "[4/6] aapt2 compile+link"
"$BT/aapt2" compile --dir "$HERE/res" -o "$OUT/res.zip"
"$BT/aapt2" link -o "$OUT/unsigned.apk" -I "$PLATFORM/android.jar" \
    --manifest "$HERE/AndroidManifest.xml" --java "$OUT/gen" "$OUT/res.zip"

echo "[5/6] add classes.dex, zipalign"
# add dex to the apk with python (no extra deps)
python3 - "$OUT/unsigned.apk" "$OUT/dex/classes.dex" <<'EOF'
import sys, zipfile
apk, dex = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(apk, 'a', zipfile.ZIP_DEFLATED) as z:
    z.write(dex, 'classes.dex')
EOF
"$BT/zipalign" -f 4 "$OUT/unsigned.apk" "$OUT/aligned.apk"

echo "[6/6] sign"
KS="$HERE/debug.keystore"
if [ ! -f "$KS" ]; then
    keytool -genkeypair -keystore "$KS" -alias nimo -keyalg RSA -keysize 2048 \
        -validity 10950 -storepass nimoautofill -keypass nimoautofill \
        -dname "CN=nimo test key" 2>/dev/null
fi
"$BT/apksigner" sign --ks "$KS" --ks-pass pass:nimoautofill \
    --out "$HERE/nimo-autofill.apk" "$OUT/aligned.apk"
echo "built: $HERE/nimo-autofill.apk"
"$BT/apksigner" verify --print-certs "$HERE/nimo-autofill.apk" | head -3
