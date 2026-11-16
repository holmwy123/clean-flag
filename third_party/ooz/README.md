# ooz (source for cleanflag/bin/ooz.dll)

`cleanflag/bin/ooz.dll` is built from these files, which come from
[powzix/ooz](https://github.com/powzix/ooz) (GPL-3.0, Copyright (C) 2016 Powzix).

The only change is one line at the end of `kraken_lib.cpp` that exports the decompressor for Python:

```cpp
extern "C" int ooz_decompress(const byte *src, size_t src_len, byte *dst, size_t dst_len){ return Kraken_Decompress(src,src_len,dst,dst_len); }
```

Build (MinGW-w64):

```
x86_64-w64-mingw32-g++ -O2 -shared -static -w -o ooz.dll kraken_lib.cpp lzna.cpp bitknit.cpp
```
