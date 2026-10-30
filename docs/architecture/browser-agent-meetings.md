# Browser Agent meeting detection

## Decision

Meeting Recorder uses Browser Agent's existing request/response channel to discover browser
meeting tabs. The browser owns its native host, profile identity, tool switches and site
permissions. Meeting Recorder owns the polling schedule. No additional Python dependency,
network listener, browser subscription or browser timer is needed.

## Behavior and entry points

`audiorecorder/meetings/browser_agent.py` discovers Windows names under `\\.\pipe\` beginning
with `browser-agent-`, or macOS `browser-agent-*.sock` paths under `~/.browser-agent/run/`.
Each request opens one connection, writes a UTF-8 JSON line and reads one response line.
The protocol version is 4. Each request has a three-second deadline and a 1 MiB response limit.
Windows uses a nonblocking byte pipe; macOS uses a Unix socket with bounded reads.

Each poll asks profiles to describe themselves, checks their protocol version and Agent access,
then calls only enabled tools: `meetings.web.googleMeet`, `meetings.web.zoom` and
`meetings.web.teams`, with caller `meeting-recorder`. Other tools are never invoked.
Tool responses must preserve the profile identity and protocol version. Each discovered profile
is queried concurrently and publishes its result independently. A failed profile contributes no
candidates for that poll; other profiles continue. A successful empty response
and an unreachable or refused profile have distinct diagnostic states.

The detector starts immediately when detection is enabled and polls every 15 seconds on a worker
thread with one request worker per profile. It retains an immutable snapshot for `scan()` and
`status()`; neither performs I/O on the
Qt thread. Stopping clears the snapshot immediately and cancels outstanding work. A stopped
worker cannot publish late results after restart. Disabling detection or quitting stops polling;
a native-call supervised test alone does not enable browser polling.

`ui/main_window.py` composes the detector with the other detectors and queues each completed
poll onto the UI thread. `ui/settings_dialog.py` displays profile labels, enabled meeting tools,
disabled access and connection failures as plain text, refreshing from the cached snapshot.

## Prompt contract

Candidates use `browser:<instanceId>:<meeting key>` as their identity, the service name, tab
title and source `browser-agent`. Duplicate tabs with the same profile and meeting key produce
one candidate. The generic `MeetingCandidate.prompt_immediately` field asks the prompt policy
to offer this candidate on its first observation, without adding the native-window stability
delay after the browser poll. Native candidates retain their existing two-observation policy.
No generic detector branches on Browser Agent's implementation or source name.

The existing lifecycle still requires the user's recording action. Browser candidates supply
no joined/end observations, and cannot trigger automatic recording or automatic stopping.
Dismissal suppresses another prompt until the candidate disappears. Permission failures clear
the affected candidates, so recovery may offer a new prompt.

## Constraints and trade-offs

- Only running profiles are discoverable. Install [Browser Agent](https://github.com/ludekvodicka/BrowserAgentExtension) and its
  native host separately, and enable Agent access and the desired Meetings tools in each profile.
  Its host is a small native binary started by the browser per profile; Meeting Recorder never
  starts or installs it.
- Browser Agent URL matching includes pre-join and ended pages; it does not prove participation.
  Titles and keys are untrusted data. The channel trusts processes of the same local OS user.
- The normal prompt delay is one 15-second poll plus response/Qt dispatch time. A slow endpoint
  cannot delay another profile's result. No strict
  15-second guarantee is possible when a profile does not respond promptly.
- Detection is supported on Windows and macOS. Linux has no Browser Agent transport here.
- Browser Agent truncates keys. Different tabs with a colliding key within a profile collapse
  to one advisory candidate. Profile identity prevents collisions between profiles.

## Verification

`tests/test_browser_agent.py` covers discovery for both platforms, enabled tools, multiple
profiles, stable identities, refusal/malformed response handling, cancellation and local peer
framing/deadlines. `tests/test_browser_agent_ui.py` exercises the real Qt/lifecycle chain and
proves a browser candidate offers a prompt without starting audio, even with automatic recording
enabled. The Settings test retains the existing no-scroll fit check.

Live-browser checks on Windows and macOS remain in the manual checklist. Local protocol fixtures
do not establish real-browser permissions or macOS installation behavior.
