"""Entry point.

  dl_transcribe.exe                     open the window
  dl_transcribe.exe FILE|FOLDER ...     open the window and transcribe these (drag onto the .exe)

Command-line mode (python -m dltr --cli ...):
  --cli [--model NAME] [--lang CODE] [--translate] [--mixed] [--speakers | --speakers=N] [--formats pdf,txt,srt,vtt,json,csv] [--outdir DIR] FILE ...
"""

import sys


def _print(*a):
    try:
        print(*a, flush=True)
    except (AttributeError, OSError):  # no console (windowed build)
        pass


def cli(args):
    from . import engine
    from .common import find_media, lang_name
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    opts = {"model": "large-v3-turbo", "language": None, "task": "transcribe"}
    formats, outdir, files = ["txt", "srt"], None, []
    it = iter(args)
    for a in it:
        if a == "--model":
            opts["model"] = next(it)
        elif a == "--lang":
            opts["language"] = next(it)
        elif a == "--translate":
            opts["task"] = "translate"
        elif a == "--mixed":
            opts["multilingual"] = True
        elif a == "--speakers":
            opts["speakers"] = True
        elif a.startswith("--speakers="):
            opts["speakers"], opts["num_speakers"] = True, int(a.split("=", 1)[1])
        elif a == "--formats":
            formats = next(it).split(",")
        elif a == "--outdir":
            outdir = next(it)
        else:
            files.append(a)
    rc = 0
    for f in find_media(files):
        try:
            r = engine.transcribe(f, opts, on_progress=lambda frac, t: _print(f"  {t}"))
            paths = engine.save(r, formats, outdir)
            extra = f", {len(r['speakers'])} speakers" if r.get("speakers") else ""
            _print(f"OK   {f}  [{lang_name(r['spoken_language'])}, {r['model']} on {r['device']}{extra}]")
            for p in paths:
                _print(f"     -> {p}")
        except Exception as e:
            rc = 1
            _print(f"FAIL {f}: {e}")
    return rc


def main():
    args = sys.argv[1:]
    if args[:1] == ["--cli"]:
        return cli(args[1:])
    from .gui import App
    App(files=args).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
