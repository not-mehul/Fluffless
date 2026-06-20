#!/usr/bin/env bash
# Generate a tiny demo library for Fluffless: one show with three "episodes"
# that share an 8-second intro at a different offset in each — exactly the
# situation the detector is built for. Requires ffmpeg.
#
#   ./generate_sample.sh   →  sample/The Daily Show/episode{1,2,3}.wav
set -euo pipefail
cd "$(dirname "$0")"
OUT="The Daily Show"
mkdir -p "$OUT"; cd "$OUT"

note() {  # $1=freq  $2=outfile — a 0.5s tone with two harmonics (timbre)
  ffmpeg -v error -y -f lavfi \
    -i "aevalsrc='0.35*sin(2*PI*$1*t)+0.18*sin(2*PI*$1*2*t)+0.09*sin(2*PI*$1*3*t)':d=0.5:s=44100" "$2"
}
melody() {  # $1=outfile  rest=freqs — concatenate notes into a melody
  local out=$1; shift; local i=0; : > _l.txt
  for f in "$@"; do note "$f" "_n${i}.wav"; echo "file _n${i}.wav" >> _l.txt; i=$((i+1)); done
  ffmpeg -v error -y -f concat -safe 0 -i _l.txt -c copy "$out"; rm -f _n*.wav _l.txt
}

# Shared intro melody (16 notes ≈ 8s) — the "fluff" to be found.
melody intro.wav 523 587 659 698 784 698 622 554 523 587 659 740 622 554 698 784
# Distinct head/tail melodies per episode so only the intro recurs.
melody head1.wav 131 139 147 156 165 147 139 131 156 175
melody tail1.wav 1568 1661 1760 1865 1976 1760 1661 1568 1865
melody head2.wav 196 208 220 233 247 220 208 196 233
melody tail2.wav 1175 1245 1319 1397 1480 1319 1245 1175 1397 1480
melody head3.wav 98 104 110 117 123 110 104 98 117 123 131 98
melody tail3.wav 2093 2217 2349 2489 2637 2349 2217 2093 2489

for i in 1 2 3; do
  printf "file head%d.wav\nfile intro.wav\nfile tail%d.wav\n" "$i" "$i" > l.txt
  ffmpeg -v error -y -f concat -safe 0 -i l.txt -c copy "episode$i.wav"
  rm -f "head$i.wav" "tail$i.wav" l.txt
done
rm -f intro.wav
echo "Created demo episodes in '$OUT/'. Point Fluffless at the 'sample' folder."
