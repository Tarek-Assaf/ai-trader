# T-178 delay structure and unmatched entries

Delay structure **no**: of the 15 entries within 30 bars, 4 were after the signal (median -7.0 bars, IQR -12.5..1.0); null p for consistent sign = 0.8086, null p for a narrow IQR = 0.2264; port-side precision 25/45, null mean 12.79. The 16 unmatched entries fail mostly on cross. A positive stable delay supports owner entries placed manually after signals; no stable delay supports a different script, version or settings.

Same-direction signed-offset histogram (owner minus signal, bars): {'-60..-55': 0, '-54..-49': 0, '-48..-43': 0, '-42..-37': 1, '-36..-31': 0, '-30..-25': 2, '-24..-19': 0, '-18..-13': 2, '-12..-7': 4, '-6..-1': 2, '+0..+5': 3, '+6..+11': 0, '+12..+17': 1, '+18..+23': 1, '+24..+29': 0, '+30..+35': 0, '+36..+41': 1, '+42..+47': 1, '+48..+53': 0, '+54..+59': 1, '+60..+60': 0, 'outside': 12}
Port-side matched signals by New York day: {'2026-10-07': 1, '2026-10-08': 3, '2026-10-11': 2, '2026-10-13': 5, '2026-10-14': 1, '2026-10-15': 11, '2026-10-16': 2}.
Unmatched entries are tabulated in the JSON with nearest any-direction signal and failed conditions.

Replay only: no broker calls, orders, holdout reads, lake writes, tuning or secrets.
