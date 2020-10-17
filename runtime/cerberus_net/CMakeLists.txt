cmake_minimum_required(VERSION 3.8 FATAL_ERROR)

include_directories(
    ${OpenCV_INCLUDE_DIRS} 
    ${PROJECT_SOURCE_DIR}/src
    ${PROJECT_SOURCE_DIR}/trt_plugins
)

file(GLOB TRT_PLUGINS_SRCS trt_plugins/*.cpp)
file(GLOB TRT_PLUGINS_CU_SRCS trt_plugins/*.cu)

# Compiling cuda sources with NVCC
set(CMAKE_CXX_FLAGS "-std=c++14 -Ofast -Wall -Wfatal-errors")
# SET(CUDA_SEPARABLE_COMPILATION ON) #  Required when compiling CUDA Kernels with Dynamic Parallelism
set(CUDA_NVCC_FLAGS "${CUDA_NVCC_FLAGS} -arch=compute_${CUDA_COMPUTE} -code=sm_${CUDA_COMPUTE}") # -rdc=true
CUDA_COMPILE(CU_OBJS ${TRT_PLUGINS_CU_SRCS})

add_library( trt_plugins_lib STATIC
    ${TRT_PLUGINS_SRCS} ${CU_OBJS}
)

file(GLOB CERB_NET_SRCS src/*.cpp)

add_library( cerberus_lib STATIC
    ${CERB_NET_SRCS}
)

add_executable (cerberus_run main.cpp)

target_link_libraries (
    cerberus_run
    trt_plugins_lib
    cerberus_lib
    ${OpenCV_LIBS}
)