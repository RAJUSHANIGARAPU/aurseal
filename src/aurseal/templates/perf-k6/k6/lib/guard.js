// Load and stress refuse a host that is not on this machine unless ALLOW_REMOTE_LOAD=1
// records that its owner agreed. `make load` runs the same rule in Python before k6
// starts (src/*/guard.py, unit-tested); this copy covers a `k6 run` typed by hand.

const LOCAL_NAMES = ['localhost', 'host.docker.internal'];
const LOAD_PROFILES = ['load', 'stress'];

export function isLocal(url) {
  const match = /^https?:\/\/(\[[^\]]+\]|[^/:?#]+)/i.exec(url);
  if (!match) {
    return false;
  }
  const host = match[1].toLowerCase();
  if (LOCAL_NAMES.includes(host) || host.endsWith('.localhost')) {
    return true;
  }
  if (host === '[::1]' || /^127(\.\d{1,3}){3}$/.test(host)) {
    return true;
  }
  // A single-label name is a Compose service next to this one; IPs and FQDNs are not.
  return !host.includes('.') && !host.startsWith('[');
}

export function assertTargetAllowed(profile, url) {
  if (!LOAD_PROFILES.includes(profile) || isLocal(url) || __ENV.ALLOW_REMOTE_LOAD === '1') {
    return;
  }
  throw new Error(
    `refusing a ${profile} run against ${url}: it is not a local target. Run the target ` +
      'locally, or set ALLOW_REMOTE_LOAD=1 once the owner of that host has agreed.',
  );
}
