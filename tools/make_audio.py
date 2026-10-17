"""Generate natural-sounding MP3s for every English line in a lesson.

Usage:
  python tools/make_audio.py              # all lessons
  python tools/make_audio.py 2026-10-12   # one or more lessons

Output:
  lessons/audio/<id>/<hash>.mp3   one clip per line
  lessons/audio/<id>.json         {"files": {"<exact text>": "<hash>.mp3"}}

The page plays these clips when they exist and falls back to the browser's own voice otherwise.
Clips already on disk are reused, so re-running only fills in what's missing.

Engine:
  ENGINE = "edge"   free, no account (edge-tts: pip install edge-tts). Unofficial, may stop working.
  ENGINE = "azure"  official Azure Speech; set env vars AZURE_SPEECH_KEY and AZURE_SPEECH_REGION.
  Both use the same Microsoft neural voices, so switching does not change how lessons sound.
"""
import asyncio, hashlib, json, os, pathlib, re, sys, urllib.request
from xml.sax.saxutils import escape

ENGINE = "edge"
VOICES = {"f": "en-AU-NatashaNeural", "m": "en-AU-WilliamMultilingualNeural"}
NARRATOR = "f"  # vocab and examples

ROOT = pathlib.Path(__file__).resolve().parent.parent
LESSONS = ROOT / "lessons"
AUDIO = LESSONS / "audio"

# must match the sentence split in index.html (runSpeech)
SENT_RE = re.compile(r'[^.!?]+[.!?]+["”\')]*\s*|[^.!?]+$')


def sentences(p):
    return [s.strip() for s in (SENT_RE.findall(p) or [p]) if s.strip()]


def lines_for(L):
    """(text, voice key) for every line the page can read aloud."""
    other = (L.get("dialogue") or {}).get("other") or {}
    them = other.get("voice", "f")
    me = "f" if them == "m" else "m"
    out = []
    for v in L.get("vocab", []):
        out.append((v["en"], NARRATOR))
        if v.get("ex"):
            out.append((v["ex"], NARRATOR))
    for x in L.get("listening", []):
        out.append((x["en"], them))
        if x.get("reply"):
            out.append((x["reply"], me))
    sp = L.get("speech") or {}
    for p in sp.get("paragraphs", []):
        out += [(s, me) for s in sentences(p)]
    for p in sp.get("patterns", []):
        out.append((p["en"], me))
    for ln in (L.get("dialogue") or {}).get("lines", []):
        out.append((ln["en"], them if ln["s"] == "o" else me))
    for r in L.get("rescue", []):
        out.append((r["en"], me))
    seen, uniq = set(), []
    for t, v in out:
        if t not in seen:
            seen.add(t)
            uniq.append((t, v))
    return uniq


def spoken(text):
    return re.sub(r"_{2,}", " blank ", text)


async def synth_edge(text, voice, path):
    import edge_tts
    await edge_tts.Communicate(spoken(text), voice).save(str(path))


async def synth_azure(text, voice, path):
    key, region = os.environ["AZURE_SPEECH_KEY"], os.environ["AZURE_SPEECH_REGION"]
    ssml = (f'<speak version="1.0" xml:lang="en-AU"><voice name="{voice}">'
            f"{escape(spoken(text))}</voice></speak>")
    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                 "User-Agent": "gday-daily-speaking"})
    data = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=60).read())
    path.write_bytes(data)


SYNTH = {"edge": synth_edge, "azure": synth_azure}[ENGINE]


async def make(lesson_id):
    L = json.loads((LESSONS / f"{lesson_id}.json").read_text(encoding="utf-8"))
    out_dir = AUDIO / lesson_id
    out_dir.mkdir(parents=True, exist_ok=True)
    files, todo = {}, []
    for text, vk in lines_for(L):
        voice = VOICES[vk]
        name = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()[:12] + ".mp3"
        files[text] = name
        if not (out_dir / name).exists():
            todo.append((text, voice, out_dir / name))

    sem = asyncio.Semaphore(4)

    async def one(text, voice, path):
        async with sem:
            for attempt in range(3):
                try:
                    tmp = path.with_suffix(".part")
                    await SYNTH(text, voice, tmp)
                    tmp.replace(path)
                    return True
                except Exception as e:
                    if attempt == 2:
                        print(f"  failed: {text[:50]!r}: {e}")
                    await asyncio.sleep(2 * (attempt + 1))
        return False

    ok = await asyncio.gather(*(one(*t) for t in todo))
    files = {t: n for t, n in files.items() if (out_dir / n).exists()}
    for f in out_dir.iterdir():  # drop clips for lines that were edited or removed
        if f.name not in files.values():
            f.unlink()
    (AUDIO / f"{lesson_id}.json").write_text(
        json.dumps({"engine": ENGINE, "files": files}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{lesson_id}: {len(files)} clips ({sum(ok)} new, {len(todo) - sum(ok)} failed)")


def main():
    ids = sys.argv[1:] or sorted(p.stem for p in LESSONS.glob("????-??-??.json"))
    for i in ids:
        asyncio.run(make(i))


if __name__ == "__main__":
    main()
