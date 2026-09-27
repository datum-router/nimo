# Google Sign-In for nimo

"Sign in with Google" is the flakiest part of mobile test automation
industry-wide. nimo handles it with a simple trick: **a human signs the
test account in once, the emulator snapshot remembers it forever.**

## One-time setup (~10 minutes, human)

1. Install a **Play Store** system image (not plain google_apis):
   ```
   sdkmanager "system-images;android-30;google_apis_playstore;x86_64"
   ```
2. Create the AVD:
   ```
   avdmanager create avd -n nimo-authed \
     -k "system-images;android-30;google_apis_playstore;x86_64" -d "pixel_6"
   ```
3. Boot it:
   ```
   emulator -avd nimo-authed -writable-system
   ```
4. On the emulator, open **Settings → Passwords & accounts → Add account →
   Google**, and sign in with nimo's test Google account
   (e.g. `nimo.tester@gmail.com`). Complete any "verify it's you" step —
   check *"Don't ask again on this device"* if offered.
5. Open the Play Store once and accept the ToS, so Play Services is fully
   initialized.
6. Save a snapshot:
   ```
   adb emu avd snapshot save nimo-authed
   ```

## Every run after that (automatic)

Boot from the snapshot and the account is already there:

```
emulator -avd nimo-authed -snapshot nimo-authed -no-snapshot-save
```

In `auth.yaml`:
```
google_account: nimo.tester@gmail.com
```

When the crawler meets a Google sign-in button it taps it, taps the
account in the system chooser, and taps through the OAuth consent screen
("Continue"/"Allow"). No human involved.

## CI (GitHub Actions)

`android-emulator-runner` supports Play Store images. Bake the snapshot
once locally, upload the AVD as a CI cache artifact, and boot with
`-snapshot`. See `.github/workflows/demo.yml` for the runner skeleton.

## Limits (honest)

- The **first** sign-in needs a human. There is no supported way to
  provision a Google account on a fresh emulator purely via adb.
- Accounts with mandatory 2FA on every new device will stall — use a
  dedicated test account and approve the emulator as a trusted device.
- Apps using passkeys/Credential Manager still show the account picker,
  which the agent handles the same way.
- If Google login fails, the pipeline falls back to provided credentials
  (`email:`/`password:` in auth.yaml), then to automatic sign-up, then
  records the screen as unreachable with the reason — never silently.
