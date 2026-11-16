#pragma once
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <stdint.h>
#include <emmintrin.h>
#include <tmmintrin.h>
typedef unsigned char byte;
typedef unsigned char uint8;
typedef unsigned int uint32;
typedef uint64_t uint64;
typedef int64_t int64;
typedef signed int int32;
typedef unsigned short uint16;
typedef signed short int16;
typedef unsigned int uint;
#define __forceinline inline __attribute__((always_inline))
#define _TCHAR char
static inline unsigned char _BitScanReverse(unsigned long *i, uint32_t m){ if(!m) return 0; *i=31-__builtin_clz(m); return 1; }
static inline unsigned char _BitScanForward(unsigned long *i, uint32_t m){ if(!m) return 0; *i=__builtin_ctz(m); return 1; }
static inline unsigned char _BitScanReverse64(unsigned long *i, uint64_t m){ if(!m) return 0; *i=63-__builtin_clzll(m); return 1; }
static inline unsigned char _BitScanForward64(unsigned long *i, uint64_t m){ if(!m) return 0; *i=__builtin_ctzll(m); return 1; }
#define _byteswap_ulong __builtin_bswap32
#define _byteswap_uint64 __builtin_bswap64
#define _byteswap_ushort __builtin_bswap16
static inline uint32_t _rotl(uint32_t x,int n){ return (x<<n)|(x>>(32-n)); }
#define __debugbreak() abort()
