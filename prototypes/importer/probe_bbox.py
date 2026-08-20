#!/usr/bin/env python3
"""PROTOTYPE — throwaway. Where exactly do two frames differ?"""
import sys

import numpy as np
from PIL import Image


def main():
    a, b = sys.argv[1], sys.argv[2]
    ia = np.asarray(Image.open(a).convert("RGB")).astype(np.int16)
    ib = np.asarray(Image.open(b).convert("RGB")).astype(np.int16)
    d = np.abs(ia - ib).max(axis=2)
    ys, xs = np.nonzero(d > 8)
    if not len(ys):
        print("identical above threshold")
        return
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    print(f"bbox x {x0}..{x1}  y {y0}..{y1}  ({x1-x0+1}x{y1-y0+1}) "
          f"strong={len(ys)}")
    pad = 8
    box = (max(0, x0 - pad), max(0, y0 - pad),
           min(ia.shape[1], x1 + pad), min(ia.shape[0], y1 + pad))
    Image.open(a).convert("RGB").crop(box).save(sys.argv[3])
    Image.open(b).convert("RGB").crop(box).save(sys.argv[4])
    print("wrote crops", sys.argv[3], sys.argv[4])


if __name__ == "__main__":
    main()
