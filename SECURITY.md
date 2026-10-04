# Security Policy

## Reporting a vulnerability

Please report security issues privately via GitHub's
[private vulnerability reporting](https://github.com/datum-router/nimo/security/advisories/new)
rather than opening a public issue. We aim to acknowledge within 72 hours.

## Threat model — read this before running nimo

nimo **drives untrusted third-party Android applications**. Treat every APK you
hand it as hostile code.

- **Run it against devices you own or are authorised to test.** nimo installs,
  launches and drives the APK you give it, and reads the device's system log.
  Only test apps you have the right to test.
- **Prefer a disposable emulator over a personal device.** The agent taps,
  types, rotates and drags; it can and will trigger destructive in-app actions
  (deleting records, sending data) because that is how bugs are found.
- **Isolate the device's network.** A hostile APK on a device with your
  credentials or LAN access is a risk nimo does not mitigate. In hosted use,
  each run gets a fresh sandboxed emulator with egress restricted.
- **Credentials.** `auth.yaml` can hold app logins for exploring past login
  walls. It is gitignored. Never commit it; prefer throwaway test accounts.
- **The agent does not exfiltrate app data.** It sends UI structure (and, with
  a vision backend, screenshots) to the configured LLM endpoint. If the app
  under test shows sensitive data, that data is in those payloads — choose your
  backend accordingly, and use a self-hosted or contracted endpoint for
  anything confidential. The default (Pollinations.ai) is an anonymous free
  service with no data agreement: fine for demos, not for sensitive apps.

## Scope

In scope: the engine's handling of APKs, logcat, device I/O, the report
renderer (HTML escaping of untrusted app text), and credential handling.

Out of scope: vulnerabilities in the apps you choose to test, and in `adb`
or the Android platform itself.
