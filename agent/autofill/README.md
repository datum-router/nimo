# nimo AutofillService

nimo's own Android `AutofillService`, installed on every test device.
It is the "global" answer to login walls: one OS-level credential filler
that works in **any** app's login form — no per-app scripting, no root,
no IME typing flakiness.

## How it works

1. `agent/autofill.py::setup()` (once per pipeline run):
   - installs `nimo-autofill.apk` (`adb install -r -g`)
   - pushes customer credentials to `/data/local/tmp/nimo-autofill.json`
   - selects the service: `settings put secure autofill_service com.nimo.autofill/.NimoAutofillService`
2. On a login screen, `auth.attempt` → `autofill.attempt_fill()`:
   - taps the username field → the service's `onFillRequest` fires
   - the service walks the `AssistStructure`, classifies username/password
     fields (autofill hints → inputType → view-id heuristics), and offers a
     single **"nimo test login"** dataset
   - the agent taps the suggestion → both fields fill via the OS autofill
     contract → the agent taps the app's login button
3. On failure (no hints, no suggestion, WebView without autofill) the
   ladder falls back to adb typing, then throwaway signup, as before.

Credentials file shape (written by the agent, re-read on every request):

```json
{"default": {"username": "test@example.com", "password": "s3cr3t"},
 "com.example.app": {"username": "u2", "password": "p2"}}
```

## Source layout

- `src/com/nimo/autofill/NimoAutofillService.java` — the service
- `src/com/nimo/autofill/FieldClassifier.java` — field classification, android-free
- `src/com/nimo/autofill/CredStore.java` — credential file parsing, android-free
- `test/TestNimo.java` — plain-JVM unit tests for the two above
- `AndroidManifest.xml`, `res/xml/autofillservice.xml`

## Building

No Gradle needed — plain SDK command-line tools:

```bash
ANDROID_SDK_ROOT=~/workspace/android-sdk ./build.sh
```

This compiles, runs the JVM tests, dexes, packages, zipaligns and signs
with `debug.keystore` (a throwaway test key committed to the repo so
rebuilds keep a stable signature). Output: `nimo-autofill.apk`.

The committed APK is what the pipeline installs; rebuild only when the
service source changes.

## Security notes

- The service holds **no** credentials itself; they are pushed per-run to
  an ephemeral CI emulator and die with the runner.
- `onSaveRequest` is a no-op — nimo never harvests credentials back.
- Test accounts only. Never point this at real user credentials.
- Limitation: apps/WebViews that don't expose autofill get no suggestion;
  the ladder's other rungs cover those.
