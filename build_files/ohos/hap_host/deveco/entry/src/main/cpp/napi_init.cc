/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "host_bridge.h"
#include "xcomponent_input.h"
#include "ime_input.h"
#include <napi/native_api.h>
#include <ace/xcomponent/native_interface_xcomponent.h>
#include <multimodalinput/oh_key_code.h>
#include <hilog/log.h>
#include <cmath>
#include <cstdio>
#include <cerrno>
#include <stdexcept>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <memory>
#include <string>
#include <unordered_set>
#include <unistd.h>
#include <sys/stat.h>
#include <vector>
namespace {
std::atomic<uint32_t> document_request{0};
std::mutex key_mutex;
std::unordered_set<int32_t> keys;
#define CHECK_KEY(name) static_assert(int(KEY_##name) == int(KEYCODE_##name), "SDK key taxonomy changed")
CHECK_KEY(A); CHECK_KEY(Z); CHECK_KEY(0); CHECK_KEY(9); CHECK_KEY(F1); CHECK_KEY(F12);
CHECK_KEY(NUMPAD_0); CHECK_KEY(NUMPAD_9); CHECK_KEY(CTRL_LEFT); CHECK_KEY(CTRL_RIGHT);
CHECK_KEY(SHIFT_LEFT); CHECK_KEY(SHIFT_RIGHT); CHECK_KEY(ALT_LEFT); CHECK_KEY(ALT_RIGHT);
CHECK_KEY(META_LEFT); CHECK_KEY(META_RIGHT); CHECK_KEY(DEL); CHECK_KEY(FORWARD_DEL);
CHECK_KEY(ENTER); CHECK_KEY(ESCAPE); CHECK_KEY(MOVE_HOME); CHECK_KEY(MOVE_END);
#undef CHECK_KEY
void check(int32_t result, const char *message)
{
  auto &bridge = blender_host_bridge();
  if (!bridge.accepting() && (result == GHOST_OHOS_CLOSED || result == GHOST_OHOS_UNAVAILABLE || result == GHOST_OHOS_BUSY)) {
    return; /* Closing admission/stale surface is expected; retain the actual drain error. */
  }
  if (result != GHOST_OHOS_OK && result != GHOST_OHOS_EMPTY) { bridge.fail(result, message); }
}
template<class Fn> void sdk_guard(Fn &&fn) noexcept
{
  try { fn(); }
  catch (...) { blender_host_bridge().fail(GHOST_OHOS_CALLBACK_FAILED, "Exception contained in XComponent callback"); }
}
bool size(OH_NativeXComponent *component, void *window, uint32_t &w, uint32_t &h)
{
  uint64_t width = 0, height = 0;
  if (OH_NativeXComponent_GetXComponentSize(component, window, &width, &height) != 0 ||
      !width || !height || width > INT32_MAX || height > INT32_MAX) { return false; }
  w = uint32_t(width); h = uint32_t(height); return true;
}
void created(OH_NativeXComponent *component, void *window)
{
  sdk_guard([&] {
    uint32_t w, h;
    if (!size(component, window, w, h)) { check(GHOST_OHOS_INVALID, "Invalid native surface size"); return; }
    { std::lock_guard<std::mutex> lock(key_mutex); keys.clear(); }
    check(blender_host_bridge().attach(window, w, h), "XComponent attach failed");
  });
}
void changed(OH_NativeXComponent *component, void *window)
{
  sdk_guard([&] {
    uint32_t w, h;
    if (!size(component, window, w, h)) { check(GHOST_OHOS_INVALID, "Invalid native surface resize"); return; }
    check(blender_host_bridge().resize(window, w, h), "XComponent resize failed");
  });
}
void destroyed(OH_NativeXComponent *, void *)
{
  /* Controlled removal already awaits async detach. Unexpected framework removal
   * must not return while Blender/native ref still uses the borrowed window.
   * There is no SDK API to defer this callback; fail-stop on a broken ACK fence.
   * Framework pre-callback owner-unref ordering remains a real-device gate. */
  int32_t result = GHOST_OHOS_CALLBACK_FAILED;
  try {
    { std::lock_guard<std::mutex> lock(key_mutex); keys.clear(); }
    result = blender_host_bridge().detach();
  } catch (...) {}
  if (result != GHOST_OHOS_OK) {
    OH_LOG_Print(LOG_APP, LOG_FATAL, 0xB10, "BlenderHost", "Surface destroy without detach ACK; aborting for ownership safety");
    std::abort();
  }
}
void touch(OH_NativeXComponent *component, void *window)
{
  sdk_guard([&] {
    uint64_t generation;
    if (!blender_host_bridge().surface(generation, window)) { return; }
    OH_NativeXComponent_TouchEvent native{};
    if (OH_NativeXComponent_GetTouchEvent(component, window, &native) != 0) {
      check(GHOST_OHOS_CALLBACK_FAILED, "Cannot read XComponent touch event"); return;
    }
    OH_NativeXComponent_EventSourceType source{};
    if (OH_NativeXComponent_GetTouchEventSourceType(component, native.id, &source) != 0) {
      check(GHOST_OHOS_CALLBACK_FAILED, "Cannot read XComponent touch source"); return;
    }
    if (source != OH_NATIVEXCOMPONENT_SOURCE_TYPE_TOUCHSCREEN) { return; } /* Avoid mouse/touch duplication. */
    GHOST_OHOSEvent event{};
    const int32_t result = blender_xcomponent_touch(native, 1, generation, event);
    if (result == GHOST_OHOS_OK) { check(blender_host_bridge().input(event), "Touch enqueue failed"); }
    else { check(result, "Touch decode failed"); }
  });
}
void mouse(OH_NativeXComponent *component, void *window)
{
  sdk_guard([&] {
    uint64_t generation;
    if (!blender_host_bridge().surface(generation, window)) { return; }
    OH_NativeXComponent_MouseEvent native{};
    if (OH_NativeXComponent_GetMouseEvent(component, window, &native) != 0) {
      check(GHOST_OHOS_CALLBACK_FAILED, "Cannot read XComponent mouse event"); return;
    }
    GHOST_OHOSEvent event{};
    const int32_t result = blender_xcomponent_mouse(native, 1, generation, event);
    if (result == GHOST_OHOS_OK) { check(blender_host_bridge().input(event), "Mouse enqueue failed"); }
    else { check(result, "Mouse decode failed"); }
  });
}
void hover(OH_NativeXComponent *, bool) {} /* Hover is not focus loss. */
void focus_event(void *window, bool focused)
{
  uint64_t generation;
  if (!blender_host_bridge().surface(generation, window)) { return; }
  if (!focused) { std::lock_guard<std::mutex> lock(key_mutex); keys.clear(); }
  GHOST_OHOSEvent event{}; event.type = GHOST_OHOS_EVENT_FOCUS;
  event.generation = generation; event.pressed = focused;
  check(blender_host_bridge().input(event), "Focus enqueue failed");
}
void focused(OH_NativeXComponent *, void *window) { sdk_guard([&] { focus_event(window, true); }); }
void blurred(OH_NativeXComponent *, void *window) { sdk_guard([&] { focus_event(window, false); }); }
void key(OH_NativeXComponent *component, void *window)
{
  sdk_guard([&] {
    uint64_t generation;
    if (!blender_host_bridge().surface(generation, window)) { return; }
    OH_NativeXComponent_KeyEvent *native = nullptr;
    OH_NativeXComponent_KeyAction action{};
    OH_NativeXComponent_KeyCode code{};
    if (OH_NativeXComponent_GetKeyEvent(component, &native) != 0 || !native ||
        OH_NativeXComponent_GetKeyEventAction(native, &action) != 0 ||
        OH_NativeXComponent_GetKeyEventCode(native, &code) != 0) {
      check(GHOST_OHOS_CALLBACK_FAILED, "Cannot read XComponent key event"); return;
    }
    if (action != OH_NATIVEXCOMPONENT_KEY_ACTION_DOWN && action != OH_NATIVEXCOMPONENT_KEY_ACTION_UP) { return; }
    GHOST_OHOSEvent event{}; event.type = GHOST_OHOS_EVENT_KEY;
    event.generation = generation; event.value = int32_t(code);
    event.pressed = action == OH_NATIVEXCOMPONENT_KEY_ACTION_DOWN;
    {
      std::lock_guard<std::mutex> lock(key_mutex);
      if (event.pressed) { event.repeat = !keys.insert(event.value).second; }
      else { keys.erase(event.value); }
    }
    check(blender_host_bridge().input(event), "Key enqueue failed");
  });
}
void axis(OH_NativeXComponent *, ArkUI_UIInputEvent *native, ArkUI_UIInputEvent_Type type)
{
  sdk_guard([&] {
    if (!native || type != ARKUI_UIINPUTEVENT_TYPE_AXIS) { return; }
    uint64_t generation;
    if (!blender_host_bridge().surface(generation)) { return; }
    const int32_t action = OH_ArkUI_AxisEvent_GetAxisAction(native);
    GHOST_OHOSEvent event{}; event.generation = generation;
    if (action == UI_AXIS_EVENT_ACTION_CANCEL) {
      event.type = GHOST_OHOS_EVENT_CANCEL_INPUT;
      check(blender_host_bridge().input(event), "Axis cancel enqueue failed"); return;
    }
    if (action != UI_AXIS_EVENT_ACTION_BEGIN && action != UI_AXIS_EVENT_ACTION_UPDATE && action != UI_AXIS_EVENT_ACTION_END) { return; }
    const double x = OH_ArkUI_PointerEvent_GetX(native), y = OH_ArkUI_PointerEvent_GetY(native);
    const double horizontal = OH_ArkUI_AxisEvent_GetHorizontalAxisValue(native);
    const double vertical = OH_ArkUI_AxisEvent_GetVerticalAxisValue(native);
    const int tool = OH_ArkUI_UIInputEvent_GetToolType(native);
    if (!std::isfinite(x) || !std::isfinite(y) || x < INT32_MIN || x > INT32_MAX ||
        y < INT32_MIN || y > INT32_MAX || !std::isfinite(horizontal) || !std::isfinite(vertical)) {
      check(GHOST_OHOS_INVALID, "Invalid ArkUI axis data"); return;
    }
    if (horizontal == 0 && vertical == 0) { return; }
    event.type = GHOST_OHOS_EVENT_WHEEL; event.x = int32_t(std::lround(x)); event.y = int32_t(std::lround(y));
    /* SDK: mouse vertical degrees, positive backward; touchpad vertical physical
     * px, positive up. Policy: 15 degrees or 40 px per detent. Horizontal is px. */
    event.wheel_x = float(horizontal / 40.0);
    if (tool == UI_INPUT_EVENT_TOOL_TYPE_MOUSE) { event.wheel_y = float(-vertical / 15.0); }
    else if (tool == UI_INPUT_EVENT_TOOL_TYPE_TOUCHPAD) { event.wheel_y = float(vertical / 40.0); }
    else { check(GHOST_OHOS_UNAVAILABLE, "Unknown axis tool; units cannot be inferred"); return; }
    check(blender_host_bridge().input(event), "ArkUI wheel enqueue failed");
  });
}
OH_NativeXComponent_Callback callbacks{created, changed, destroyed, touch};
OH_NativeXComponent_MouseEvent_Callback mouse_callbacks{mouse, hover};
void register_component(napi_env env, napi_value exports)
{
  bool exists = false;
  if (napi_has_named_property(env, exports, OH_NATIVE_XCOMPONENT_OBJ, &exists) != napi_ok || !exists) { return; }
  napi_value value;
  OH_NativeXComponent *component = nullptr;
  if (napi_get_named_property(env, exports, OH_NATIVE_XCOMPONENT_OBJ, &value) != napi_ok ||
      napi_unwrap(env, value, reinterpret_cast<void **>(&component)) != napi_ok || !component) {
    check(GHOST_OHOS_INVALID, "Cannot unwrap native XComponent"); return;
  }
  check(OH_NativeXComponent_RegisterCallback(component, &callbacks), "Register surface callbacks failed");
  check(OH_NativeXComponent_RegisterMouseEventCallback(component, &mouse_callbacks), "Register mouse callbacks failed");
  check(OH_NativeXComponent_RegisterFocusEventCallback(component, focused), "Register focus callback failed");
  check(OH_NativeXComponent_RegisterBlurEventCallback(component, blurred), "Register blur callback failed");
  check(OH_NativeXComponent_RegisterKeyEventCallback(component, key), "Register key callback failed");
  check(OH_NativeXComponent_RegisterUIInputEventCallback(component, axis, ARKUI_UIINPUTEVENT_TYPE_AXIS), "Register ArkUI axis callback failed");
}
napi_value undefined(napi_env env) { napi_value value; napi_get_undefined(env, &value); return value; }
void require(bool condition, const char *message) { if (!condition) { throw std::runtime_error(message); } }
std::vector<napi_value> arguments(napi_env env, napi_callback_info info, size_t expected)
{
  size_t count = 0;
  require(napi_get_cb_info(env, info, &count, nullptr, nullptr, nullptr) == napi_ok && count == expected, "Incorrect argument count");
  std::vector<napi_value> result(count);
  require(napi_get_cb_info(env, info, &count, result.data(), nullptr, nullptr) == napi_ok, "Cannot read arguments");
  return result;
}
std::string string(napi_env env, napi_value value)
{
  size_t bytes = 0;
  require(napi_get_value_string_utf8(env, value, nullptr, 0, &bytes) == napi_ok && bytes <= 1024 * 1024, "Expected bounded UTF8 string");
  std::string result(bytes + 1, '\0');
  require(napi_get_value_string_utf8(env, value, result.data(), result.size(), &bytes) == napi_ok, "Cannot decode string");
  result.resize(bytes); require(result.find('\0') == std::string::npos, "Embedded NUL is invalid");
  return result;
}
double number(napi_env env, napi_value value)
{
  double result = 0;
  require(napi_get_value_double(env, value, &result) == napi_ok && std::isfinite(result), "Expected finite number"); return result;
}
bool boolean(napi_env env, napi_value value)
{
  bool result = false; require(napi_get_value_bool(env, value, &result) == napi_ok, "Expected boolean"); return result;
}
void success(int32_t result) { require(result == GHOST_OHOS_OK, ("Blender host error " + std::to_string(result)).c_str()); }
napi_value configure(napi_env env, napi_callback_info info)
{
  auto args = arguments(env, info, 6);
  const double scale = number(env, args[4]), dpi = number(env, args[5]);
  require(scale > 0 && scale < 100 && dpi >= 1 && dpi <= UINT16_MAX && std::trunc(dpi) == dpi, "Invalid display density");
  success(blender_host_bridge().configure(string(env,args[0]),string(env,args[1]),string(env,args[2]),string(env,args[3]),float(scale),uint32_t(dpi)));
  return undefined(env);
}
napi_value state(napi_env env, napi_callback_info info)
{
  arguments(env,info,0); napi_value value; napi_create_int32(env,blender_host_bridge().state(),&value); return value;
}
napi_value result(napi_env env, napi_callback_info info)
{
  arguments(env,info,0); napi_value value; napi_create_int32(env,blender_host_bridge().result(),&value); return value;
}
napi_value foreground(napi_env env, napi_callback_info info)
{
  auto args=arguments(env,info,1); blender_host_bridge().foreground(boolean(env,args[0])); return undefined(env);
}
napi_value commit(napi_env env, napi_callback_info info)
{
  auto args=arguments(env,info,1); success(blender_host_bridge().text(string(env,args[0]))); return undefined(env);
}
napi_value take_document_request(napi_env env, napi_callback_info info)
{
  arguments(env, info, 0); napi_value value;
  napi_create_uint32(env, document_request.exchange(0), &value); return value;
}
napi_value ime_active(napi_env env, napi_callback_info info)
{
  arguments(env, info, 0); napi_value value; napi_get_boolean(env, blender_ime_active(), &value); return value;
}
napi_value ime_geometry(napi_env env, napi_callback_info info)
{
  auto args = arguments(env, info, 3);
  const double id = number(env, args[0]), left = number(env, args[1]), top = number(env, args[2]);
  require(id > 0 && id <= INT32_MAX && std::trunc(id) == id &&
          left >= INT32_MIN && left <= INT32_MAX && top >= INT32_MIN && top <= INT32_MAX,
          "Invalid IME window geometry");
  blender_ime_geometry(int32_t(id), left, top); return undefined(env);
}
napi_value wheel(napi_env env, napi_callback_info info)
{
  auto args=arguments(env,info,4); GHOST_OHOSEvent event{}; event.type=GHOST_OHOS_EVENT_WHEEL;
  const double x=number(env,args[0]),y=number(env,args[1]),wx=number(env,args[2]),wy=number(env,args[3]);
  require(x>=INT32_MIN&&x<=INT32_MAX&&y>=INT32_MIN&&y<=INT32_MAX&&std::abs(wx)<=10000&&std::abs(wy)<=10000,"Wheel input out of range");
  event.x=int32_t(std::lround(x)); event.y=int32_t(std::lround(y)); event.wheel_x=float(wx); event.wheel_y=float(wy);
  success(blender_host_bridge().input(event)); return undefined(env);
}
struct Async { napi_async_work work{}; napi_deferred deferred{}; int32_t result=0; bool shutdown=false; };
void execute(napi_env, void *opaque)
{
  auto *operation=static_cast<Async *>(opaque);
  try { operation->result=operation->shutdown ? blender_host_bridge().shutdown() : blender_host_bridge().detach(); }
  catch (...) { operation->result=GHOST_OHOS_CALLBACK_FAILED; }
}
void complete(napi_env env,napi_status status,void *opaque)
{
  std::unique_ptr<Async> operation(static_cast<Async *>(opaque));
  napi_value value;
  if(status==napi_ok && operation->result==GHOST_OHOS_OK) {
    napi_get_undefined(env,&value); napi_resolve_deferred(env,operation->deferred,value);
  } else {
    char message[128];
    std::snprintf(message, sizeof(message), "Blender lifecycle coordinator failed: %d", operation->result);
    napi_value text; napi_create_string_utf8(env,message,NAPI_AUTO_LENGTH,&text);
    napi_create_error(env,nullptr,text,&value); napi_reject_deferred(env,operation->deferred,value);
  }
  napi_delete_async_work(env,operation->work);
}
napi_value lifecycle(napi_env env,napi_callback_info info,bool shutdown)
{
  arguments(env,info,0);
  /* Release queued asset waiters before a lifecycle worker needs the same SDK
   * worker pool. Never join or interrupt a running Blender operator on UI. */
  if (shutdown) { blender_host_bridge().begin_shutdown(); }
  auto operation=std::make_unique<Async>(); operation->shutdown=shutdown;
  napi_value promise,name;
  require(napi_create_promise(env,&operation->deferred,&promise)==napi_ok,"Cannot create lifecycle promise");
  napi_create_string_utf8(env,"BlenderLifecycle",NAPI_AUTO_LENGTH,&name);
  require(napi_create_async_work(env,nullptr,name,execute,complete,operation.get(),&operation->work)==napi_ok,"Cannot create lifecycle work");
  if(napi_queue_async_work(env,operation->work)!=napi_ok) { napi_delete_async_work(env,operation->work); throw std::runtime_error("Cannot queue lifecycle work"); }
  operation.release(); return promise;
}
struct FileAsync {
  napi_async_work work = nullptr;
  napi_deferred deferred = nullptr;
  std::shared_ptr<FileCommands::Ticket> ticket;
  BlenderOHOSFileResult result{};
  FileAsync() { result.status = GHOST_OHOS_CALLBACK_FAILED; }
};
void file_execute(napi_env, void *opaque) noexcept
{
  auto *operation = static_cast<FileAsync *>(opaque);
  try {
    /* Arming here prevents an engine operation if SDK work is cancelled before
     * execute starts. UI only submits an owned, initially unarmed POD ticket. */
    blender_host_bridge().arm_file(operation->ticket);
    operation->result = operation->ticket->wait();
  }
  catch (...) {
    blender_host_bridge().cancel_file(operation->ticket);
    operation->result.status = GHOST_OHOS_CALLBACK_FAILED;
    std::snprintf(operation->result.message, sizeof(operation->result.message), "异步文件操作失败");
  }
}
void file_complete(napi_env env, napi_status status, void *opaque) noexcept
{
  std::unique_ptr<FileAsync> operation(static_cast<FileAsync *>(opaque));
  napi_value value;
  if (status != napi_ok) { blender_host_bridge().cancel_file(operation->ticket); }
  if (status == napi_cancelled || (status == napi_ok && (operation->result.status == 0 || operation->result.status == 1))) {
    napi_create_int32(env, status == napi_cancelled ? 1 : operation->result.status, &value);
    napi_resolve_deferred(env, operation->deferred, value);
  }
  else {
    napi_value text;
    const char *message = status == napi_ok && operation->result.message[0] ?
        operation->result.message : "异步文件操作已取消或执行失败";
    napi_create_string_utf8(env, message, NAPI_AUTO_LENGTH, &text);
    napi_create_error(env, nullptr, text, &value); napi_reject_deferred(env, operation->deferred, value);
  }
  napi_delete_async_work(env, operation->work);
}
napi_value file_operation(napi_env env, napi_callback_info info)
{
  auto args = arguments(env, info, 2);
  const double kind = number(env, args[0]);
  require(kind >= 1 && kind <= 6 && kind == std::trunc(kind), "无效的文件操作");
  const std::string path = string(env, args[1]);
  auto operation = std::make_unique<FileAsync>();
  napi_value promise, name;
  require(napi_create_promise(env, &operation->deferred, &promise) == napi_ok, "Cannot create file promise");
  napi_create_string_utf8(env, "BlenderFileOperation", NAPI_AUTO_LENGTH, &name);
  require(napi_create_async_work(env, nullptr, name, file_execute, file_complete, operation.get(), &operation->work) == napi_ok,
          "Cannot create file work");
  int32_t queued;
  try { queued = blender_host_bridge().submit_file(uint32_t(kind), path, operation->ticket); }
  catch (...) { napi_delete_async_work(env, operation->work); throw; }
  if (queued != GHOST_OHOS_OK) {
    napi_delete_async_work(env, operation->work);
    require(false, queued == GHOST_OHOS_FULL ? "文件任务队列已满，请等待当前操作完成" :
                   queued == GHOST_OHOS_INVALID ? "无效的工作副本" : "Blender尚未准备好或已经关闭");
  }
  if (napi_queue_async_work(env, operation->work) != napi_ok) {
    blender_host_bridge().cancel_file(operation->ticket); napi_delete_async_work(env, operation->work);
    throw std::runtime_error("Cannot queue file work");
  }
  operation.release(); return promise;
}
napi_value detach(napi_env env,napi_callback_info info) { return lifecycle(env,info,false); }
napi_value shutdown(napi_env env,napi_callback_info info) { return lifecycle(env,info,true); }
/* Validate mappings to OS-installed HAP libraries. Python's ExtensionFileLoader
 * reads them in place; app-data symbolic links are denied on the actual 2in1. */
void bind_one(const std::string &runtime,const std::string &native_root,const std::string &relative,const std::string &library)
{
  require(!relative.empty()&&relative[0]!='/'&&relative.size()<4096,"Invalid module relative path");
  for (const unsigned char c : relative) {
    require(c >= 32 && c != 127 && c != '\\', "Invalid module path character");
  }
  size_t begin = 0;
  while (true) {
    const size_t end = relative.find('/', begin);
    const std::string piece = relative.substr(begin, end == std::string::npos ? end : end - begin);
    require(!piece.empty() && piece != "." && piece != "..", "Invalid module path component");
    if (end == std::string::npos) { break; }
    begin = end + 1;
  }
  require(library.size() > 6 && library.substr(0,3) == "lib" &&
          library.substr(library.size()-3) == ".so" && library.find("..") == std::string::npos,
          "Invalid native library basename");
  for (const unsigned char c : library) {
    require((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '_' || c == '.' || c == '-',
            "Invalid native library basename");
  }
  struct stat staging_stat{};
  require(lstat(runtime.c_str(), &staging_stat) == 0 && S_ISDIR(staging_stat.st_mode),
          "Cannot open extraction staging root");
  const std::string target=native_root+"/"+library;
  struct stat native_stat{};
  require(stat(target.c_str(),&native_stat)==0&&S_ISREG(native_stat.st_mode),"Signed native module is missing");
}
napi_value bind(napi_env env,napi_callback_info info)
{
  auto args=arguments(env,info,4);
  const std::string runtime=string(env,args[0]),native_root=string(env,args[1]);
  require(!runtime.empty()&&runtime[0]=='/'&&!native_root.empty()&&native_root[0]=='/',"Native library roots must be absolute");
  bool a=false,b=false; uint32_t count=0,names=0;
  require(napi_is_array(env,args[2],&a)==napi_ok&&a&&napi_is_array(env,args[3],&b)==napi_ok&&b,"Expected native arrays of module paths/names");
  require(napi_get_array_length(env,args[2],&count)==napi_ok&&napi_get_array_length(env,args[3],&names)==napi_ok&&count==names&&count<=65536,"Invalid module mapping length");
  for(uint32_t i=0;i<count;++i) {
    napi_value path = nullptr, name = nullptr;
    require(napi_get_element(env,args[2],i,&path)==napi_ok&&napi_get_element(env,args[3],i,&name)==napi_ok,"Cannot read module mapping");
    bind_one(runtime,native_root,string(env,path),string(env,name));
  }
  return undefined(env);
}
template<napi_value(*Function)(napi_env,napi_callback_info)> napi_value contained(napi_env env,napi_callback_info info) noexcept
{
  try { return Function(env,info); }
  catch(const std::exception &error) { napi_throw_error(env,nullptr,error.what()); }
  catch(...) { napi_throw_error(env,nullptr,"Native Blender host exception"); }
  return nullptr;
}
napi_value initialize(napi_env env,napi_value exports) noexcept
{
  try {
    napi_property_descriptor methods[]={
      {"configure",nullptr,contained<configure>,nullptr,nullptr,nullptr,napi_default,nullptr},
      {"state",nullptr,contained<state>,nullptr,nullptr,nullptr,napi_default,nullptr},
      {"result",nullptr,contained<result>,nullptr,nullptr,nullptr,napi_default,nullptr},
      {"foreground",nullptr,contained<foreground>,nullptr,nullptr,nullptr,napi_default,nullptr},
      {"commitText",nullptr,contained<commit>,nullptr,nullptr,nullptr,napi_default,nullptr},
       {"takeDocumentRequest",nullptr,contained<take_document_request>,nullptr,nullptr,nullptr,napi_default,nullptr},
       {"imeActive",nullptr,contained<ime_active>,nullptr,nullptr,nullptr,napi_default,nullptr},
       {"imeGeometry",nullptr,contained<ime_geometry>,nullptr,nullptr,nullptr,napi_default,nullptr},
      {"wheel",nullptr,contained<wheel>,nullptr,nullptr,nullptr,napi_default,nullptr},
      {"detach",nullptr,contained<detach>,nullptr,nullptr,nullptr,napi_default,nullptr},
      {"shutdown",nullptr,contained<shutdown>,nullptr,nullptr,nullptr,napi_default,nullptr},
      {"fileOperation",nullptr,contained<file_operation>,nullptr,nullptr,nullptr,napi_default,nullptr},
      {"bindLibraries",nullptr,contained<bind>,nullptr,nullptr,nullptr,napi_default,nullptr}};
    require(napi_define_properties(env,exports,sizeof(methods)/sizeof(methods[0]),methods)==napi_ok,"Cannot export native host");
    register_component(env,exports); return exports;
  } catch(...) { napi_throw_error(env,nullptr,"Blender native module initialization failed"); return nullptr; }
}
}
// Blender menu operators request a provider dialog; the ArkUI owner handles its
// URI grant and copies through authorized file descriptors on the UI side.
extern "C" __attribute__((visibility("default"))) int32_t blender_hap_request_document(uint32_t operation) noexcept
{
  if (operation < 1 || operation > 6 || blender_host_bridge().state() != 2 || !blender_host_bridge().accepting())
    return GHOST_OHOS_UNAVAILABLE;
  uint32_t empty = 0;
  return document_request.compare_exchange_strong(empty, operation) ? GHOST_OHOS_OK : GHOST_OHOS_BUSY;
}
static napi_module module={1,0,nullptr,initialize,"blender_host",nullptr,{0}};
extern "C" __attribute__((constructor,visibility("default"))) void blender_host_register(void) noexcept
{
  napi_module_register(&module);
}
