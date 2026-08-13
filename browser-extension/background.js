"use strict";

const BRIDGE_URL = "http://127.0.0.1:8765/v1/meeting";
const HEADER_VALUE = "browser-extension-v1";
const meetings = new Map();

function reportPresence() {
  return report({type: "companion-presence"});
}

async function report(meeting) {
  try {
    await fetch(BRIDGE_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Meeting-Recorder": HEADER_VALUE
      },
      body: JSON.stringify(meeting)
    });
  } catch (_error) {
    // The desktop app may not be running. The next heartbeat retries quietly.
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "meeting-recorder") {
    return;
  }
  let current = null;
  port.onMessage.addListener((message) => {
    if (message?.type !== "meeting-state" || typeof message.key !== "string") {
      return;
    }
    current = message;
    if (current.active === false) {
      meetings.delete(port);
    } else {
      meetings.set(port, current);
    }
    report(current);
  });
  port.onDisconnect.addListener(() => {
    meetings.delete(port);
    if (current) {
      report({...current, active: false});
    }
  });
});

chrome.alarms.create("meeting-heartbeats", {periodInMinutes: 0.5});
reportPresence();
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== "meeting-heartbeats") {
    return;
  }
  reportPresence();
  for (const meeting of meetings.values()) {
    report(meeting);
  }
});
