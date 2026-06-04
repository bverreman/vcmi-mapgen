"""The faithful map representation shared by extraction, measurement, generation, and
the writer. A map is terrain (per-tile structured) + objects (authoritative ids +
animation + mask). Anything in this shape round-trips to an editor-valid .vmap.
"""
import json, re, glob, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vmapwrite

TCODE={0:"dt",1:"sa",2:"gr",3:"sn",4:"sw",5:"rg",6:"sb",7:"lv",8:"wt",9:"rc"}
RIVER={1:"clrv",2:"icyrv",3:"mudrv",4:"lavrv"}
ROAD ={1:"dirtrd",2:"gravrd",3:"cobbrd"}
def _mir(m): h,v=m&1,m&2; return "+" if (h and v) else "|" if v else "-" if h else "_"

def tile_string(c):
    s=f"{TCODE.get(c['t'],'gr')}{c['view']}{_mir(c.get('m',0))}"
    if c.get('rt'): s+=f"{RIVER.get(c['rt'],'clrv')}{c.get('rd',0)}_"
    if c.get('ot'): s+=f"{ROAD.get(c['ot'],'dirtrd')}{c.get('od',0)}_"
    return s

def visitable_from(mask):
    """The 3x3 approach grid VCMI needs for visitable templates. Buildings (have
    blocked body) are entered from the sides/below; free-standing pickups/monsters
    from all 8 directions. None for pure decoration (no visitable tile)."""
    if not any('A' in r for r in mask): return None
    if any('B' in r for r in mask): return ["---", "+-+", "+++"]
    return ["+++", "+-+", "+++"]

def to_vmap(fm, out_path, name=None):
    """faithful map dict -> editor-valid .vmap via the proven writer."""
    levels=[[[tile_string(c) for c in row] for row in lvl] for lvl in fm["terrain"]]
    objs=[]; n=0
    for o in fm["objects"]:
        if not o.get("type"): continue
        n+=1
        tmpl={"animation":o["animation"],"editorAnimation":"","mask":o["mask"]}
        vf=visitable_from(o["mask"])
        if vf: tmpl["visitableFrom"]=vf
        objs.append({"instanceName":f"{o['type']}_{n}","l":o["l"],"type":o["type"],"subtype":o["subtype"],
                     "template":tmpl,"x":o["x"],"y":o["y"]})
    header,_,_,_=vmapwrite.read_raw(glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap")[0])
    for pid,pl in list(header.get("players",{}).items()):
        if isinstance(pl,dict): pl["mainTown"]=None
    vmapwrite.write_vmap(out_path, header, levels, objs, name=name or fm.get("name","generated"))
    return out_path

def load(path): return json.load(open(path))
def save(fm, path):
    os.makedirs(os.path.dirname(path), exist_ok=True); json.dump(fm, open(path,"w"))
