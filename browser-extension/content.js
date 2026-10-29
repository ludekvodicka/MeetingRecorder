(() => {
  "use strict";

  function candidateForPage() {
    const host = location.hostname.toLowerCase();
    const path = location.pathname;
    let appName = null;

    if (host === "meet.google.com" && /^\/[a-z]{3}-[a-z]{4}-[a-z]{3}(?:\/|$)/i.test(path)) {
      appName = "Google Meet";
    } else if (host.endsWith(".zoom.us") && path.startsWith("/wc/")) {
      appName = "Zoom Web";
    } else if ((host === "teams.microsoft.com" || host === "teams.cloud.microsoft") &&
               /\/(meetup-join|meet|call)\b/i.test(path + location.hash)) {
      appName = "Microsoft Teams Web";
    }

    if (!appName) {
      return null;
    }
    const routeIdentity = appName === "Microsoft Teams Web" ? path + location.hash : path;
    return {
      type: "meeting-state",
      active: true,
      key: `${host}${routeIdentity}`.slice(0, 300),
      app_name: appName,
      title: (document.title || `${appName} meeting`).slice(0, 300)
    };
  }

  let port = null;
  let current = null;

  function connect() {
    const connected = chrome.runtime.connect({name: "meeting-recorder"});
    port = connected;
    connected.onDisconnect.addListener(() => {
      if (port === connected) {
        port = null;
      }
    });
  }

  function send(message) {
    try {
      port.postMessage(message);
      return true;
    } catch (_error) {
      port = null;
      return false;
    }
  }

  function reconcile() {
    const candidate = candidateForPage();
    if (!candidate) {
      if (port && current) {
        const existing = port;
        send({...current, active: false});
        existing.disconnect();
      }
      port = null;
      current = null;
      return;
    }
    if (port && current && current.key !== candidate.key) {
      send({...current, active: false});
    }
    current = candidate;
    if (!port) {
      connect();
    }
    if (!send(current)) {
      connect();
      send(current);
    }
  }

  reconcile();
  const watcher = setInterval(reconcile, 5000);
  window.addEventListener("pagehide", () => clearInterval(watcher), {once: true});
})();
