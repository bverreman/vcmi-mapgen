"""Faithful h3m -> .vmap converter using VCMI's OWN config for object identifiers
(src/vcmi_ids.py) -- no reverse-engineering. Round-trips a real map so it can be
opened in the editor and confirmed faithful, then trusted as the reference.
"""
import json, glob, os, sys, re, collections
sys.path.insert(0,"src"); import h3m, vcmi_ids, vmapwrite
ROOT="/mnt/data/workspace/vcmi-mapgen"

TCODE={0:"dt",1:"sa",2:"gr",3:"sn",4:"sw",5:"rg",6:"sb",7:"lv",8:"wt",9:"rc"}
RIVER={1:"clrv",2:"icyrv",3:"mudrv",4:"lavrv"}
ROAD ={1:"dirtrd",2:"gravrd",3:"cobbrd"}

def mirror_suffix(m):
    h,v=m&1,m&2
    return "+" if (h and v) else "|" if v else "-" if h else "_"

def tile_string(t):
    s=f"{TCODE.get(t.terrain,'gr')}{t.view}{mirror_suffix(t.mirror)}"
    if t.river_type: s+=f"{RIVER.get(t.river_type,'clrv')}{t.river_dir}_"
    if t.road_type:  s+=f"{ROAD.get(t.road_type,'dirtrd')}{t.road_dir}_"
    return s

def build_mask(block_mask, visit_mask):
    # 6 rows x 8 cols. block bit 1=passable/0=blocked; visit bit 1=visitable.
    grid=[['V']*8 for _ in range(6)]
    for r in range(6):
        for c in range(8):
            blocked = not (block_mask[r]>>(7-c))&1
            visit   = (visit_mask[r]>>(7-c))&1
            grid[r][c]='A' if visit else ('B' if blocked else 'V')
    rows=[r for r in range(6) if any(ch!='V' for ch in grid[r])]
    cols=[c for c in range(8) if any(grid[r][c]!='V' for r in range(6))]
    if not rows: return ["B"]
    return ["".join(grid[r][c] for c in range(min(cols),max(cols)+1)) for r in range(min(rows),max(rows)+1)]

def convert(h3m_path, out_path):
    m=h3m.parse_file(h3m_path)
    levels=[[[tile_string(t) for t in row] for row in lvl] for lvl in m.terrain]
    objs=[]; skip=collections.Counter(); n=0
    for o in m.objects:
        r=vcmi_ids.resolve(o.obj_class, o.obj_subclass)
        if not r: skip[o.obj_class]+=1; continue
        vtype, sub = r
        anim=re.sub(r'\.(def|DEF)$','',o.animation)
        tmpl=m.templates[o.template_index]
        n+=1
        objs.append({"instanceName":f"{vtype}_{n}","l":o.l,"type":vtype,"subtype":sub,
                     "template":{"animation":anim,"editorAnimation":"",
                                 "mask":build_mask(tmpl.block_mask,tmpl.visit_mask)},
                     "x":o.x,"y":o.y})
    header,_,_,_=vmapwrite.read_raw(glob.glob("/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/RandomMaps/*.vmap")[0])
    for pid,pl in list(header.get("players",{}).items()):
        if isinstance(pl,dict): pl["mainTown"]=None
    vmapwrite.write_vmap(out_path, header, levels, objs, name=os.path.basename(h3m_path).replace(".h3m",""))
    print(f"converted {os.path.basename(h3m_path)}  {m.width}x{m.height} 2lvl={m.two_level}  objects={len(objs)}  skipped={sum(skip.values())} {dict(skip)}")
    return out_path

if __name__=="__main__":
    src=sys.argv[1] if len(sys.argv)>1 else "/home/gabriel/.var/app/eu.vcmi.VCMI/data/vcmi/Maps/Elbow Room.h3m"
    out=f"{ROOT}/out/REAL_{os.path.basename(src).replace('.h3m','')}.vmap"
    convert(src,out)
