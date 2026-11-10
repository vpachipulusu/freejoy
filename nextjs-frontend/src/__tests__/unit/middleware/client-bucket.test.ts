import { describe, expect, it } from 'vitest';
import { clientBucket } from '@/lib/middleware/client-bucket';

/** Mirrors fastapi_backend/app/core/rate_limit.py `client_bucket`: the two limiters must key a client alike. */
describe('clientBucket', () => {
  it('leaves an IPv4 address alone', () => {
    expect(clientBucket('203.0.113.7')).toBe('203.0.113.7');
  });

  it('groups an IPv6 address by its /64', () => {
    const bucket = clientBucket('2001:db8:1:2::1');

    expect(bucket).toBe('2001:db8:1:2::/64');
    expect(clientBucket('2001:db8:1:2:ffff:ffff:ffff:ffff')).toBe(bucket);
    expect(clientBucket('2001:0DB8:0001:0002:aaaa:bbbb:cccc:dddd')).toBe(bucket);
  });

  it('keeps two different /64s apart', () => {
    expect(clientBucket('2001:db8:1:2::1')).not.toBe(clientBucket('2001:db8:1:3::1'));
  });

  it('counts an IPv4-mapped IPv6 address as the IPv4 address, in either spelling', () => {
    expect(clientBucket('::ffff:203.0.113.7')).toBe('203.0.113.7');
    expect(clientBucket('::ffff:cb00:7107')).toBe('203.0.113.7');
    expect(clientBucket('0:0:0:0:0:ffff:203.0.113.7')).toBe('203.0.113.7');
  });

  it('handles the compressed forms', () => {
    expect(clientBucket('::1')).toBe('0:0:0:0::/64');
    expect(clientBucket('2001:db8::')).toBe('2001:db8:0:0::/64');
    expect(clientBucket('::')).toBe('0:0:0:0::/64');
  });

  it('strips an IPv6 zone', () => {
    expect(clientBucket('fe80::1%eth0')).toBe('fe80:0:0:0::/64');
  });

  it('returns anything that is not an address unchanged, never throwing', () => {
    expect(clientBucket('not-an-ip')).toBe('not-an-ip');
    expect(clientBucket('')).toBe('');
    expect(clientBucket('1:2:3:4:5:6:7:8:9')).toBe('1:2:3:4:5:6:7:8:9');
  });
});
