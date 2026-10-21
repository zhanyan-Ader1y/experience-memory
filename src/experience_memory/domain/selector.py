"""Deterministic UTF-8 budget selection. No I/O, models, truncation or U updates."""
from dataclasses import dataclass
import unicodedata
from .models import WindowEntry
from .errors import MemoryError,ErrorCode


def validate_summary(value):
    if (not isinstance(value,str) or not value.strip() or len(value.encode('utf-8'))>65536
            or any(unicodedata.category(ch).startswith('C') or ch in '\u2028\u2029' for ch in value)
            or any(token in value for token in ('【','】','<','>'))):
        raise MemoryError(ErrorCode.INVALID_INPUT)
    return value


@dataclass(frozen=True)
class MaterializedEntry:
    entry: WindowEntry
    line: str


@dataclass(frozen=True)
class RetentionSelection:
    selected: tuple[MaterializedEntry,...]
    dropped: tuple[MaterializedEntry,...]
    rendered_context: str
    payload_bytes: int
    lines: tuple[str,...]


def select_retention(entries,fixed_context,budget_bytes):
    if type(budget_bytes) is not int or not 1<=budget_bytes<=8192 or fixed_context.count('{entries}')!=1:
        raise MemoryError(ErrorCode.INVALID_INPUT)
    if len(fixed_context.replace('{entries}','').encode('utf-8'))>budget_bytes:
        raise MemoryError(ErrorCode.CORE_OVER_BUDGET)
    dedup={}
    for item in entries:
        validate_summary(item.line)
        if not isinstance(item.entry,WindowEntry): raise MemoryError(ErrorCode.INVALID_INPUT)
        previous=dedup.get(item.entry.experience_id)
        if previous and item.entry.last_used_seq==previous.entry.last_used_seq and item!=previous:
            raise MemoryError(ErrorCode.CONFLICT)
        if previous is None or item.entry.last_used_seq>previous.entry.last_used_seq:
            dedup[item.entry.experience_id]=item
    selected=list(dedup.values());dropped=[]
    def rank(item): return int(item.entry.priority.value[1])
    def render():
        selected.sort(key=lambda item:(rank(item),-item.entry.last_used_seq,item.entry.experience_id))
        lines=tuple('【留存:'+item.entry.priority.value+'|'+item.entry.experience_id+'|U'+str(item.entry.last_used_seq)+'】'+item.line for item in selected)
        text=fixed_context.replace('{entries}','\n'.join(lines))
        return text,lines
    while True:
        text,lines=render()
        if len(text.encode('utf-8'))<=budget_bytes:break
        victim=min(selected,key=lambda item:(-rank(item),item.entry.last_used_seq,item.entry.experience_id))
        selected.remove(victim);dropped.append(victim)
    return RetentionSelection(tuple(selected),tuple(dropped),text,len(text.encode('utf-8')),lines)
