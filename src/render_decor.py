"""Render a vmap's terrain + decoration COLOURED BY Phi-class, to reveal clustering
(pine groves, mountain ranges) that the purpose-only renderer hides."""
import sys, os, hashlib, colorsys
from PIL import Image, ImageDraw
sys.path.insert(0,"src"); import vmapwrite, vmaplib, render, generate4 as G4
TILE=9
def type_color(t):
    s=t.lower()
    def has(*k): return any(w in s for w in k)
    if has("oak","pine","tree","spruce","willow","yucca","palm"): return (30,120,40)   # trees green
    if has("mountain"): return (115,95,80)                                              # mountain brown
    if has("rock","outcrop","stony","canyon","cliff"): return (120,120,120)             # rock grey
    if has("deadveg","dead","shrub","bush","stump","log","mound","hedge","moss"): return (120,110,65) # scrub
    if has("cactus"): return (80,150,60)
    if has("flower","mandrake"): return (230,120,200)
    if has("mushroom"): return (180,90,200)
    if has("reef","kelp","lake","willowtree"): return (40,160,170)                      # water decor teal
    if has("crater","lava","volcano","fissure"): return (170,60,40)                     # volcanic
    if has("sand","dune"): return (210,190,120)
    return (90,110,70)

def render_typed(vmap_path, out_png):
    h,surf,under,objs=vmapwrite.read_raw(vmap_path)
    grids=[surf]+([under] if under else [])
    imgs=[]
    for li,g in enumerate(grids):
        H=len(g); W=len(g[0]); img=Image.new("RGB",(W*TILE,H*TILE),(0,0,0)); px=img.load()
        for y,row in enumerate(g):
            for x,c in enumerate(row):
                t=vmaplib.TERR.get(c[:2],2); r,gg,b=render.TERRAIN_RGB.get(t,(0,0,0))
                for dy in range(TILE):
                    for dx in range(TILE): px[x*TILE+dx,y*TILE+dy]=(r,gg,b)
        d=ImageDraw.Draw(img)
        for o in objs:
            if o.get("l",0)!=li: continue
            m=o.get("template",{}).get("mask",[])
            if not m or any('A' in rr for rr in m): continue   # decoration only
            col=type_color(o.get("type",""))
            cx,cy=o["x"]*TILE+TILE//2,o["y"]*TILE+TILE//2
            d.ellipse([cx-2,cy-2,cx+2,cy+2],fill=col)
        imgs.append(img)
    W=sum(i.width for i in imgs)+12*(len(imgs)-1); H=max(i.height for i in imgs)
    canvas=Image.new("RGB",(W,H),(20,20,20)); x=0
    for i in imgs: canvas.paste(i,(x,0)); x+=i.width+12
    canvas.save(out_png); return out_png

if __name__=="__main__":
    p=sys.argv[1] if len(sys.argv)>1 else "/mnt/data/workspace/vcmi-mapgen/out/gen4_lift.vmap"
    out=p.replace(".vmap","_typed.png").replace("/out/","/out/render/")
    render_typed(p,out); print("wrote",out)
