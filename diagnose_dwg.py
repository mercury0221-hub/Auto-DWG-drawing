# -*- coding: utf-8 -*-
"""
diagnose_dwg.py — 현재 열린 DWG의 엔티티 구조를 분석한다.
AutoCAD를 실행하고 DWG를 연 상태에서 실행.
"""
import sys, io, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import comtypes
    comtypes.CoInitialize()
except Exception:
    pass

from pyautocad import Autocad, APoint

DIV = "─" * 60

acad = Autocad(create_if_not_exists=False)
doc  = acad.doc
ms   = acad.model  # ModelSpace

print(DIV)
print(f"도면 : {doc.Name}")
print(DIV)

# ── 1. 전체 엔티티 타입별 카운트 ─────────────────────────────
type_count = {}
all_entities = []
for ent in ms:
    t = ent.ObjectName
    type_count[t] = type_count.get(t, 0) + 1
    all_entities.append(ent)

print(f"\n[1] 전체 엔티티: {len(all_entities)}개")
for t, c in sorted(type_count.items(), key=lambda x: -x[1]):
    print(f"    {t:40s}: {c}개")

# ── 2. 레이어 목록 ───────────────────────────────────────────
print(f"\n[2] 레이어 ({doc.Layers.Count}개)")
for i in range(min(doc.Layers.Count, 30)):
    layer = doc.Layers.Item(i)
    print(f"    [{i:2d}] {layer.Name:30s}  켜짐={not layer.LayerOn}  잠김={layer.Lock}")

# ── 3. 닫힌 폴리라인 분석 ────────────────────────────────────
print(f"\n[3] 닫힌 폴리라인 (Closed Polyline) 분석")

closed_plines = []
for ent in ms:
    name = ent.ObjectName
    if name in ("AcDbPolyline", "AcDb2dPolyline", "AcDb3dPolyline"):
        try:
            if ent.Closed:
                coords = list(ent.Coordinates)
                # (x,y) 쌍 추출
                pts = [(coords[i], coords[i+1]) for i in range(0, len(coords)-1, 2)]
                if len(pts) < 2:
                    continue
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                bbox = (min(xs), min(ys), max(xs), max(ys))
                w = bbox[2] - bbox[0]
                h = bbox[3] - bbox[1]
                area = w * h  # bbox 면적 (근사)
                closed_plines.append({
                    "layer": ent.Layer,
                    "bbox": bbox,
                    "w": w, "h": h,
                    "pts": len(pts),
                    "area": area,
                })
        except Exception as e:
            pass

closed_plines.sort(key=lambda x: -x["area"])
print(f"    총 {len(closed_plines)}개")
for i, p in enumerate(closed_plines[:15]):
    bx = p["bbox"]
    print(f"    [{i:2d}] layer={p['layer']:20s}  "
          f"bbox=({bx[0]:.0f},{bx[1]:.0f})~({bx[2]:.0f},{bx[3]:.0f})  "
          f"크기={p['w']:.0f}×{p['h']:.0f}  꼭짓점={p['pts']}개")

# ── 4. 블록 참조 ─────────────────────────────────────────────
print(f"\n[4] 블록 참조 (AcDbBlockReference)")
blocks = {}
for ent in ms:
    if ent.ObjectName == "AcDbBlockReference":
        try:
            name = ent.Name
            blocks[name] = blocks.get(name, 0) + 1
        except Exception:
            pass
if blocks:
    for n, c in sorted(blocks.items(), key=lambda x: -x[1])[:10]:
        print(f"    {n:30s}: {c}개")
else:
    print("    (블록 없음)")

# ── 5. 텍스트/MText ─────────────────────────────────────────
print(f"\n[5] 텍스트 샘플 (최대 10개)")
count = 0
for ent in ms:
    if ent.ObjectName in ("AcDbText", "AcDbMText") and count < 10:
        try:
            txt = ent.TextString if ent.ObjectName == "AcDbText" else ent.Contents
            pos = ent.InsertionPoint
            print(f"    '{txt[:40]}' @ ({pos[0]:.0f},{pos[1]:.0f})")
            count += 1
        except Exception:
            pass
if count == 0:
    print("    (텍스트 없음)")

# ── 6. LLM에 현재 전달되는 요약 vs 가능한 개선안 ─────────────
print(f"\n{DIV}")
print("[6] 현재 LLM 컨텍스트 vs 개선 가능 컨텍스트")
print(DIV)
print("\n  [현재] 3줄 요약:")
print(f"    도면 파일: {doc.Name}")
try:
    ins = int(doc.GetVariable("INSUNITS"))
    unit_map = {0:"없음",1:"inch",2:"ft",4:"mm",5:"cm",6:"m"}
    print(f"    도면 단위: {unit_map.get(ins, str(ins))}")
except Exception:
    print("    도면 단위: 알 수 없음")
try:
    emin = doc.GetVariable("EXTMIN")
    emax = doc.GetVariable("EXTMAX")
    print(f"    도면 범위: ({emin[0]:.0f},{emin[1]:.0f})~({emax[0]:.0f},{emax[1]:.0f})")
except Exception:
    print("    도면 범위: 취득 불가")

print("\n  [개선 가능] 공간 컨텍스트 포함:")
if closed_plines:
    biggest = closed_plines[0]
    bx = biggest["bbox"]
    print(f"    건물 외곽 추정: ({bx[0]:.0f},{bx[1]:.0f})~({bx[2]:.0f},{bx[3]:.0f})  "
          f"{biggest['w']:.0f}×{biggest['h']:.0f}mm  layer={biggest['layer']}")
    for i, p in enumerate(closed_plines[1:6]):
        bx = p["bbox"]
        cx = (bx[0]+bx[2])/2
        cy = (bx[1]+bx[3])/2
        print(f"    공간[{i+1}] center=({cx:.0f},{cy:.0f})  "
              f"크기={p['w']:.0f}×{p['h']:.0f}mm  layer={p['layer']}")
else:
    print("    닫힌 폴리라인 없음 — 선분(Line) 기반 도면이면 다른 추출 방법 필요")

print(f"\n{DIV}")
print("진단 완료")
print(DIV)
