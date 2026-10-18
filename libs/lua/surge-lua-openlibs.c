/*
** Surge XT replacement for Lua 5.1.5's linit.c.
**
** Derived from lua-5.1.5/src/linit.c, Copyright (C) 1994-2012 Lua.org, PUC-Rio,
** used under the terms of the MIT license (see lua-5.1.5/COPYRIGHT).
**
** The only change from upstream is the addition of Lua BitOp's "bit" module to
** the table of libraries that luaL_openlibs() opens. LuaJIT opens "bit" from its
** own luaL_openlibs(), and Surge's formula sandbox whitelists it (see the
** sandboxLibraryTables list in src/common/LuaSupport.cpp), so opening it here is
** what keeps the two backends at parity. Doing it in this file rather than in
** Surge means none of the lua_State creation sites in Surge need to know which
** backend they are talking to.
**
** Upstream's linit.c is deliberately left out of the vendored source drop so
** that this file is the only definition of luaL_openlibs(). See README.md.
*/

#define linit_c
#define LUA_LIB

#include "lua.h"

#include "lauxlib.h"
#include "lualib.h"

/* Lua BitOp's entry point. Not declared in lualib.h, as it is not a PUC Lua
** library; bit.c registers the global "bit" table itself via luaL_register()
** and ignores the module name this table passes to it. */
LUALIB_API int luaopen_bit(lua_State *L);

static const luaL_Reg lualibs[] = {{"", luaopen_base},
                                  {LUA_LOADLIBNAME, luaopen_package},
                                  {LUA_TABLIBNAME, luaopen_table},
                                  {LUA_IOLIBNAME, luaopen_io},
                                  {LUA_OSLIBNAME, luaopen_os},
                                  {LUA_STRLIBNAME, luaopen_string},
                                  {LUA_MATHLIBNAME, luaopen_math},
                                  {LUA_DBLIBNAME, luaopen_debug},
                                  {"bit", luaopen_bit},
                                  {NULL, NULL}};

LUALIB_API void luaL_openlibs(lua_State *L)
{
    const luaL_Reg *lib = lualibs;
    for (; lib->func; lib++)
    {
        lua_pushcfunction(L, lib->func);
        lua_pushstring(L, lib->name);
        lua_call(L, 1, 0);
    }
}
