Captured real BMS frames go here as `*.hex` files (one 300-byte frame, hex, spaces allowed).

Capture them on the Pi with `python -m jkbms_pi --config ... --dump-frames`; the dumps land in
`frames/` next to the database. `tests/test_protocol.py` checks that every fixture has a valid
checksum and decodes. Add value assertions that match the JK app for the frames you capture (§15 step 5).
