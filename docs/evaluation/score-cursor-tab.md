# Original-time score cursor and TAB view

The experimental saved-MIDI viewer uses absolute player ticks and the public
alphaTab playback schedule and rendered bounds. It interpolates only between
surrounding note/rest columns, including tie continuations and simultaneous
voices. It does not extrapolate past the next column. A new line can move left;
normal forward playback within one bar cannot. Seek/stop is stateless, and the
path is rebuilt on layout changes. Invalid reverse geometry is reported rather
than clamped into a misleading position.

The alphaTab 1.7.1 default animated cursor doubles its target distance and
transition duration to avoid pauses. With irregular performance spacing, the
continuing transition can overshoot and then return left at a later beat. This
was reproduced in the second bar of an existing local saved result. The local
score model and note/rest positions themselves remained ordered. No package
source or dependency version is changed.

`/midi-trial/saved/<id>?view=tab` explicitly selects staff plus TAB. The plain
route still defaults to staff only. The notation trial also links to this view.
It uses the saved low-to-high tuning and fret limit, with the existing simple
highest-playable-string rule. This is a display mapping, distinct from the
optional movement-aware fingering derivative. Unknown tuning, out-of-range
notes, overlapping use of one string, percussion, multiple staves, capo and
transposition stop TAB playback with a visible reason; originals remain
available. No source file is modified or new inference requested.

The rendered staff uses sounding pitches, with bass clef. Storage-clock tempo
and 4/4 containers do not establish the real song's tempo or meter. Printed
spacing is not a linear time axis, so pixel speed varies while playback time
advances regularly. The arbitrary durations of this interchange view are not
claimed to be a musically quantized, publishable rhythm score. GP5 quarter-note
preview downloads remain explicitly distinct from this original-time view.

Regression coverage: `frontend/src/test/scoreGeometry.test.ts` uses synthetic
XML with rests, irregular durations, a 10 ms note, repeat attacks, ties, bar
crossings and reflow. It verifies monotonic positions, exact anchor placement,
seek/reset/repeat, simultaneous columns, 4/5/6-string pitch/time preservation,
unmapped notes and collisions. Viewer/page tests cover old callback rejection,
failed TAB readiness, stable configuration, explicit selection and original
download retention. No private recording or MIDI fixture is committed.
