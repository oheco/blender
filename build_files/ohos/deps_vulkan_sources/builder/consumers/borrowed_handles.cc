// Metadata-copy test only: native opaque types remain incomplete, never faked.
// SafeStruct borrows handles; caller lifetime/reference management is unchanged.
#define VK_USE_PLATFORM_OHOS
#include <vulkan/utility/vk_safe_struct.hpp>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <new>
#include <type_traits>

template<class T,class=void> struct complete_type : std::false_type {};
template<class T> struct complete_type<T,std::void_t<decltype(sizeof(T))>> : std::true_type {};
static_assert(!complete_type<OH_NativeBuffer>::value && !complete_type<OHNativeWindow>::value,
              "SDK native handle types must stay opaque; fake struct definitions are forbidden");
static size_t allocations=0, frees=0, forbidden_deletes=0;
static bool is_handle(const void *p) { const auto v=reinterpret_cast<uintptr_t>(p); return v==0x1110 || v==0x2220 || v==0x3330 || v==0x4440; }
void *operator new(size_t n) { if(void *p=std::malloc(n ? n : 1)){++allocations;return p;} throw std::bad_alloc(); }
void *operator new[](size_t n) { return ::operator new(n); }
void operator delete(void *p) noexcept { if(!p)return; if(is_handle(p)){++forbidden_deletes;return;} ++frees;std::free(p); }
void operator delete[](void *p) noexcept { ::operator delete(p); }
void operator delete(void *p,size_t) noexcept { ::operator delete(p); }
void operator delete[](void *p,size_t) noexcept { ::operator delete(p); }
static void check(bool result,const char *message) { if(!result){std::fprintf(stderr,"borrowed-handle failure: %s\n",message);std::exit(1);} }

template<class Safe,class Raw,class Handle>
static void lifecycle(VkStructureType stype,Handle Raw::*rawfield,Handle Safe::*safefield,uintptr_t a,uintptr_t b)
{
  const size_t before_alloc=allocations,before_free=frees;
  {
    Raw first{};first.sType=stype;first.*rawfield=reinterpret_cast<Handle>(a);
    Raw second{};second.sType=stype;second.*rawfield=reinterpret_cast<Handle>(b);
    Raw null{};null.sType=stype;
    Safe s(&first);check(s.*safefield==first.*rawfield,"raw constructor identity");
    Safe c(s);check(c.*safefield==first.*rawfield,"copy constructor identity");
    Safe assigned(&second);assigned=s;check(assigned.*safefield==first.*rawfield,"assignment replaces borrowed pointer");
    const auto *self=&assigned;assigned=*self;check(assigned.*safefield==first.*rawfield,"self assignment identity");
    assigned.initialize(&second);check(assigned.*safefield==second.*rawfield,"initialize raw replaces borrowed pointer");
    assigned.initialize(&null);check(assigned.*safefield==nullptr,"initialize null does not free old handle");
    Safe initialized;initialized.initialize(&s);check(initialized.*safefield==first.*rawfield,"initialize safe identity");
    Safe nullsafe(&null),nullcopy(nullsafe),defaultsafe;
    defaultsafe=nullsafe;check(!(defaultsafe.*safefield) && !(nullcopy.*safefield),"null copy/assignment");
    Safe initialize_null;initialize_null.initialize(&nullsafe);check(!(initialize_null.*safefield),"initialize safe null");
  }
  check(allocations==before_alloc && frees==before_free && !forbidden_deletes,"plain borrowed lifecycle allocates/frees no handle or heap storage");
}

static void callback_chain()
{
  const size_t before_alloc=allocations,before_free=frees;
  unsigned callbacks=0;
  {
    VkFormat colors[2]={VK_FORMAT_R8G8B8A8_UNORM,VK_FORMAT_B8G8R8A8_UNORM};
    VkNativeBufferUsageOHOS usage{VK_STRUCTURE_TYPE_NATIVE_BUFFER_USAGE_OHOS};usage.OHOSNativeBufferUsage=0x12345678;
    VkPipelineRenderingCreateInfo rendering{VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO};
    rendering.pNext=&usage;rendering.colorAttachmentCount=2;rendering.pColorAttachmentFormats=colors;
    vku::PNextCopyState state;
    state.init=[&](VkBaseOutStructure *dst,const VkBaseOutStructure *source) {
      check(source->sType==VK_STRUCTURE_TYPE_PIPELINE_RENDERING_CREATE_INFO,"real custom-init callback input type");
      ++callbacks;
      auto *safe=reinterpret_cast<vku::safe_VkPipelineRenderingCreateInfo *>(dst);
      auto *raw=reinterpret_cast<const VkPipelineRenderingCreateInfo *>(source);
      auto *owned=new VkFormat[raw->colorAttachmentCount];
      for(uint32_t i=0;i<raw->colorAttachmentCount;i++)owned[i]=raw->pColorAttachmentFormats[i];
      owned[0]=VK_FORMAT_R16G16B16A16_SFLOAT;
      safe->pColorAttachmentFormats=owned;
      return true;
    };
    VkImportNativeBufferInfoOHOS import{VK_STRUCTURE_TYPE_IMPORT_NATIVE_BUFFER_INFO_OHOS};
    import.buffer=reinterpret_cast<OH_NativeBuffer *>(uintptr_t(0x1110));import.pNext=&rendering;
    auto assert_chain=[&](const void *chain) {
      check(chain && chain!=&rendering,"pNext deep-copy identity");
      auto *copy=static_cast<const VkPipelineRenderingCreateInfo *>(chain);
      check(copy->colorAttachmentCount==2 && copy->pColorAttachmentFormats!=colors && copy->pColorAttachmentFormats[0]==VK_FORMAT_R16G16B16A16_SFLOAT,"callback-owned formats preserved");
      auto *next=static_cast<const VkNativeBufferUsageOHOS *>(copy->pNext);
      check(next && next!=&usage && next->sType==usage.sType && next->OHOSNativeBufferUsage==usage.OHOSNativeBufferUsage,"second pNext deep-copy");
    };
    vku::safe_VkImportNativeBufferInfoOHOS safe(&import,&state);
    check(callbacks==1 && safe.buffer==import.buffer,"constructor forwards callback and borrows handle");assert_chain(safe.pNext);
    vku::safe_VkImportNativeBufferInfoOHOS copy(safe);assert_chain(copy.pNext);check(copy.pNext!=safe.pNext && copy.buffer==import.buffer,"copy owns independent pNext but same handle");
    vku::safe_VkImportNativeBufferInfoOHOS assigned;assigned=copy;assert_chain(assigned.pNext);
    vku::safe_VkImportNativeBufferInfoOHOS initialized;initialized.initialize(&copy);assert_chain(initialized.pNext);
    import.buffer=reinterpret_cast<OH_NativeBuffer *>(uintptr_t(0x2220));
    safe.initialize(&import,&state);check(callbacks==2 && safe.buffer==import.buffer,"reinitialize frees old chain, forwards callback, and replaces borrowed handle");assert_chain(safe.pNext);
    vku::safe_VkImportNativeBufferInfoOHOS nochain(&import,&state,false);check(!nochain.pNext && callbacks==2 && nochain.buffer==import.buffer,"copy_pnext false keeps handle but skips callback/chain");
    check(colors[0]==VK_FORMAT_R8G8B8A8_UNORM && usage.OHOSNativeBufferUsage==0x12345678,"caller metadata remains untouched");
  }
  check(callbacks==2 && !forbidden_deletes && allocations-before_alloc==frees-before_free,"real pNext/callback allocations released exactly; no borrowed handle delete");
}
int main()
{
  lifecycle<vku::safe_VkImportNativeBufferInfoOHOS>(VK_STRUCTURE_TYPE_IMPORT_NATIVE_BUFFER_INFO_OHOS,&VkImportNativeBufferInfoOHOS::buffer,&vku::safe_VkImportNativeBufferInfoOHOS::buffer,0x1110,0x2220);
  lifecycle<vku::safe_VkSurfaceCreateInfoOHOS>(VK_STRUCTURE_TYPE_SURFACE_CREATE_INFO_OHOS,&VkSurfaceCreateInfoOHOS::window,&vku::safe_VkSurfaceCreateInfoOHOS::window,0x3330,0x4440);
  callback_chain();
  check(!forbidden_deletes,"no borrowed handle ownership transfer");
  std::printf("{\"opaqueNativeTypesRemainIncomplete\":true,\"borrowedLifecycle\":true,\"nullAndSelfAssignment\":true,\"handleAllocations\":0,\"handleDeletes\":%zu,\"extraRefUnref\":false,\"realPNextCopyFree\":true,\"realCustomInitCallback\":true,\"pNextHeapBalanced\":true}\n",forbidden_deletes);
  return 0;
}
