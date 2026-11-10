import { isIP } from 'node:net';

/**
 * The rate-limit key part for a client address, mirroring the backend's `client_bucket`
 * (fastapi_backend/app/core/rate_limit.py): IPv4 is unchanged, IPv6 is grouped by its /64 (one subscriber
 * usually holds a whole /64, so a per-address key would give one client 2**64 buckets), and an IPv4-mapped IPv6
 * address counts as the IPv4 address. Anything that is not an address comes back unchanged.
 */
const GROUPS = 8;
const IPV4_MAPPED_PREFIX = [0, 0, 0, 0, 0, 0xffff];
const PREFIX_GROUPS = 4;

/** The eight 16-bit groups of an IPv6 address (any compressed or dotted-tail spelling), or null. */
function ipv6Groups(address: string): number[] | null {
  let text = address;
  const dotted = /^(.*:)(\d+\.\d+\.\d+\.\d+)$/.exec(text);
  if (dotted) {
    const octets = dotted[2].split('.').map(Number);
    if (octets.some((octet) => octet > 255)) return null;
    text = `${dotted[1]}${((octets[0] << 8) | octets[1]).toString(16)}:${((octets[2] << 8) | octets[3]).toString(16)}`;
  }
  const halves = text.split('::');
  if (halves.length > 2) return null;
  const head = halves[0] === '' ? [] : halves[0].split(':');
  const tail = halves.length === 2 && halves[1] !== '' ? halves[1].split(':') : [];
  const missing = GROUPS - head.length - tail.length;
  if (halves.length === 1 ? missing !== 0 : missing < 1) return null;
  const groups = [...head, ...Array<string>(halves.length === 2 ? missing : 0).fill('0'), ...tail];
  const values = groups.map((group) =>
    /^[0-9a-f]{1,4}$/i.test(group) ? parseInt(group, 16) : NaN
  );
  return values.length === GROUPS && values.every((value) => !Number.isNaN(value)) ? values : null;
}

export function clientBucket(address: string): string {
  const host = address.includes(':') ? address.split('%', 1)[0] : address;
  if (isIP(host) === 4) return host;
  const groups = isIP(host) === 6 ? ipv6Groups(host) : null;
  if (groups === null) return address;
  if (IPV4_MAPPED_PREFIX.every((value, index) => groups[index] === value)) {
    return `${groups[6] >> 8}.${groups[6] & 255}.${groups[7] >> 8}.${groups[7] & 255}`;
  }
  return `${groups
    .slice(0, PREFIX_GROUPS)
    .map((group) => group.toString(16))
    .join(':')}::/64`;
}
