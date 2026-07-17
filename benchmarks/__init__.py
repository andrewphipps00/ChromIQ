"""Engine evaluation harness (issue #123, W0).

Dev-only tooling — never imported by the app. The synthetic ground-truth
battery is the primary referee for every candidate improvement to the
maximum-accuracy engine mode; real measurements are secondary smoke tests
(see README.md for the interpretation rule).
"""
