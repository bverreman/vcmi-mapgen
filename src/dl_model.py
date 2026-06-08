"""Small fully-convolutional placement net (trains from scratch on CPU).

Dilated residual convs keep full resolution (no down/upsampling), so per-tile
placement detail is preserved and the receptive field still grows enough to see
distance-to-water / distance-to-edge context. ~150K params -> minutes/epoch on CPU.
Input  : [B, C_IN, H, W] terrain features
Output : [B, NPUR, H, W] non-negative per-purpose density (softplus)
"""

import torch
import torch.nn as nn
from dl_data import C_IN, NPUR


class ResDil(nn.Module):
    def __init__(self, ch, d):
        super().__init__()
        self.c1 = nn.Conv2d(ch, ch, 3, padding=d, dilation=d)
        self.c2 = nn.Conv2d(ch, ch, 3, padding=d, dilation=d)
        self.act = nn.ReLU(inplace=True)

    def forward(self, x):
        h = self.act(self.c1(x))
        h = self.c2(h)
        return self.act(x + h)


class PlacementNet(nn.Module):
    def __init__(self, ch=48, dilations=(1, 2, 4, 8, 1, 2)):
        super().__init__()
        self.stem = nn.Sequential(nn.Conv2d(C_IN, ch, 3, padding=1), nn.ReLU(inplace=True))
        self.blocks = nn.Sequential(*[ResDil(ch, d) for d in dilations])
        self.head = nn.Conv2d(ch, NPUR, 1)
        self.sp = nn.Softplus()

    def forward(self, x):
        return self.sp(self.head(self.blocks(self.stem(x))))


class DoubleConv(nn.Module):
    def __init__(self, ci, co):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(ci, co, 3, padding=1), nn.ReLU(inplace=True),
            nn.Conv2d(co, co, 3, padding=1), nn.ReLU(inplace=True))

    def forward(self, x):
        return self.net(x)


class GlobalCtx(nn.Module):
    """Adds a whole-map summary to every cell so the net 'sees the full map at once'
    (global avg-pool -> 1x1 conv -> broadcast back). This is what crops + a local
    receptive field could never provide."""
    def __init__(self, ch):
        super().__init__()
        self.fc = nn.Sequential(nn.Conv2d(ch, ch, 1), nn.ReLU(inplace=True), nn.Conv2d(ch, ch, 1))

    def forward(self, x):
        g = x.mean(dim=(2, 3), keepdim=True)
        return x + self.fc(g)


class PlacementUNet(nn.Module):
    """Full-map U-Net: two 1/2 downsamples (all corpus sizes 36/72/108/144 are /4) +
    a global-context bottleneck -> global receptive field. One forward pass turns a
    whole terrain into the whole placement heatmap. ~no BatchNorm (trains at batch=1)."""
    def __init__(self, ch=48):
        super().__init__()
        self.stem = nn.Sequential(nn.Conv2d(C_IN, ch, 3, padding=1), nn.ReLU(inplace=True))
        self.e1 = DoubleConv(ch, ch)
        self.e2 = DoubleConv(ch, ch * 2)
        self.pool = nn.MaxPool2d(2)
        self.bott = DoubleConv(ch * 2, ch * 3)
        self.gctx = GlobalCtx(ch * 3)
        self.up2 = nn.ConvTranspose2d(ch * 3, ch * 2, 2, stride=2)
        self.d2 = DoubleConv(ch * 2 + ch * 2, ch * 2)
        self.up1 = nn.ConvTranspose2d(ch * 2, ch, 2, stride=2)
        self.d1 = DoubleConv(ch + ch, ch)
        self.head = nn.Conv2d(ch, NPUR, 1)
        self.sp = nn.Softplus()

    def forward(self, x, mask=None):
        x0 = self.stem(x)
        e1 = self.e1(x0)
        e2 = self.e2(self.pool(e1))
        b = self.gctx(self.bott(self.pool(e2)))
        d2 = self.d2(torch.cat([self.up2(b), e2], 1))
        d1 = self.d1(torch.cat([self.up1(d2), e1], 1))
        out = self.sp(self.head(d1))
        if mask is not None:                 # post-process: force NOTHING (zero density)
            out = out * mask                 # outside the real map -> no objects, ever
        return out


def n_params(m):
    return sum(p.numel() for p in m.parameters())
