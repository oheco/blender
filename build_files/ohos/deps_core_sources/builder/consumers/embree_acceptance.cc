#include <embree4/rtcore.h>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
struct Vertex { float x,y,z; };
struct Triangle { unsigned a,b,c; };
struct Filter { bool reject=false; unsigned calls=0; };
static void filter(const RTCFilterFunctionNArguments* args) {
  auto* state=static_cast<Filter*>(args->geometryUserPtr);
  for(unsigned i=0;i<args->N;++i) if(args->valid[i]) {
    ++state->calls;
    if(state->reject) args->valid[i]=0;
  }
}
static RTCRayHit ray(float x,float y,unsigned mask=~0u) {
  RTCRayHit hit{};
  hit.ray.org_x=x;hit.ray.org_y=y;hit.ray.org_z=-2;
  hit.ray.dir_z=1;hit.ray.tnear=0;hit.ray.tfar=100;hit.ray.time=0;hit.ray.mask=mask;
  hit.hit.geomID=RTC_INVALID_GEOMETRY_ID;
  for(unsigned &id:hit.hit.instID)id=RTC_INVALID_GEOMETRY_ID;
  return hit;
}
int main() {
  RTCDevice device=rtcNewDevice("threads=2");assert(device);
  assert(rtcGetDeviceError(device)==RTC_ERROR_NONE);
  assert(rtcGetDeviceProperty(device,RTC_DEVICE_PROPERTY_FILTER_FUNCTION_SUPPORTED));
  RTCScene scene=rtcNewScene(device);assert(scene);
  RTCGeometry geometry=rtcNewGeometry(device,RTC_GEOMETRY_TYPE_TRIANGLE);assert(geometry);
  auto* v=static_cast<Vertex*>(rtcSetNewGeometryBuffer(geometry,RTC_BUFFER_TYPE_VERTEX,0,RTC_FORMAT_FLOAT3,sizeof(Vertex),3));
  auto* t=static_cast<Triangle*>(rtcSetNewGeometryBuffer(geometry,RTC_BUFFER_TYPE_INDEX,0,RTC_FORMAT_UINT3,sizeof(Triangle),1));
  assert(v&&t);v[0]={0,0,0};v[1]={1,0,0};v[2]={0,1,0};t[0]={0,1,2};
  Filter state;
  rtcSetGeometryUserData(geometry,&state);
  rtcSetGeometryMask(geometry,1);
  rtcSetGeometryIntersectFilterFunction(geometry,filter);
  rtcSetGeometryOccludedFilterFunction(geometry,filter);
  rtcCommitGeometry(geometry);
  unsigned id=rtcAttachGeometry(scene,geometry);assert(id!=RTC_INVALID_GEOMETRY_ID);
  rtcCommitScene(scene);
  RTCIntersectArguments args;rtcInitIntersectArguments(&args);
  RTCRayHit hit=ray(.25f,.25f,1);rtcIntersect1(scene,&hit,&args);
  assert(hit.hit.geomID==id && hit.hit.primID==0);
  assert(std::abs(hit.ray.tfar-2.0f)<1e-6f);
  assert(std::abs(hit.hit.u-.25f)<1e-6f && std::abs(hit.hit.v-.25f)<1e-6f);
  assert(state.calls>0);
  auto miss=ray(2,2,1);rtcIntersect1(scene,&miss,&args);assert(miss.hit.geomID==RTC_INVALID_GEOMETRY_ID);
  auto mask=ray(.25f,.25f,2);rtcIntersect1(scene,&mask,&args);assert(mask.hit.geomID==RTC_INVALID_GEOMETRY_ID);
  state.reject=true;auto rejected=ray(.25f,.25f,1);rtcIntersect1(scene,&rejected,&args);
  assert(rejected.hit.geomID==RTC_INVALID_GEOMETRY_ID);
  RTCOccludedArguments occ;rtcInitOccludedArguments(&occ);
  state.reject=false;auto blocked=ray(.25f,.25f,1).ray;rtcOccluded1(scene,&blocked,&occ);
  assert(std::isinf(blocked.tfar)&&blocked.tfar<0);
  state.reject=true;auto transparent=ray(.25f,.25f,1).ray;rtcOccluded1(scene,&transparent,&occ);
  assert(transparent.tfar==100);
  assert(rtcGetDeviceError(device)==RTC_ERROR_NONE);
  // Recoverable invalid API enum must reach the real error reporting path.
  RTCGeometry invalid=rtcNewGeometry(device,static_cast<RTCGeometryType>(9999));
  // The pinned Embree implementation reports UNKNOWN for an unrecognized enum.
  assert(!invalid);assert(rtcGetDeviceError(device)==RTC_ERROR_UNKNOWN);
  rtcSetGeometryTimeStepCount(geometry,std::numeric_limits<unsigned>::max());
  assert(rtcGetDeviceError(device)==RTC_ERROR_INVALID_ARGUMENT);
  state.reject=false;auto recovery=ray(.25f,.25f,1);rtcIntersect1(scene,&recovery,&args);
  assert(recovery.hit.geomID==id && rtcGetDeviceError(device)==RTC_ERROR_NONE);
  rtcReleaseGeometry(geometry);rtcReleaseScene(scene);rtcReleaseDevice(device);
  std::cout<<"Embree triangle hit/miss/barycentric/ray-mask/intersect+occluded-filter/error=PASS\n";
}
