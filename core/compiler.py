"""
core/compiler.py - Edit plan to cutlist compiler
"""

from core.schemas import EditPlanV1


class CutSegment:
    def __init__(self, start: float, end: float):
        self.start = start
        self.end = end


class CutList:
    def __init__(self, segments=None):
        self.segments = segments or []


def plan_to_cutlist(plan: EditPlanV1) -> CutList:
    segments = []
    if hasattr(plan, 'cuts') and plan.cuts:
        for cut in plan.cuts:
            segments.append(CutSegment(
                start=cut.get('start', 0),
                end=cut.get('end', 0)
            ))
    else:
        segments.append(CutSegment(start=0, end=9999))
    return CutList(segments=segments)


def command_to_edit_plan(command: str) -> dict:
    text = command.lower().strip()
    plan = {
        "cut_silence": False,
        "remove_fillers": False,
        "add_subtitles": False,
        "add_music": False,
        "output_aspect": None,
    }
    if any(w in text for w in ["bosluk", "sessiz", "silence", "kes"]):
        plan["cut_silence"] = True
    if any(w in text for w in ["sey", "filler", "dolgu", "temizle"]):
        plan["remove_fillers"] = True
    if any(w in text for w in ["altyazi", "subtitle"]):
        plan["add_subtitles"] = True
    if any(w in text for w in ["muzik", "music", "fon"]):
        plan["add_music"] = True
    if any(w in text for w in ["shorts", "dikey", "9:16"]):
        plan["output_aspect"] = "9:16"
    if any(w in text for w in ["hepsi", "hepsini", "full"]):
        plan["cut_silence"] = True
        plan["remove_fillers"] = True
        plan["add_subtitles"] = True
    if not plan["cut_silence"] and not plan["remove_fillers"]:
        plan["cut_silence"] = True
        plan["remove_fillers"] = True
    return plan