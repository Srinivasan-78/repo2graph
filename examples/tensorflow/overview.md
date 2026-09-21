<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌​‌​​‌‌​‌​‌​​​​​‌​​‌​‌​​‌‌‌​​‌‌​‌‌​‌‌​​​‌​​​‌​‌​‌‌‌​‌​​​‌‌​‌‌‌‌​‌​‌​​‌​​‌​​‌‌‌‌​‌​​​‌‌‌​​‌‌​‌​​​‌​​​‌​​​‌​‌​‌‌‌​‌​‌​​​‌​‌​​​‌‌​​‌​​​​​‌​‌‌‌‌​​​​‌​‌​​‌​​‌​‌​​​​​‌​​‌​‌​​‌‌​‌​‌‌⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.SPJslEtoROG4DWQFAxRPJk
-->
# Repo map: tensorflow

files: 1022  nodes: 20641  edges: 96013
languages: cpp=426, c=296, python=247, proto=34, tpl=1, md=1, pyx=1

## Most depended-on files
- tensorflow/core/framework/types.h (in=199)
- tensorflow/python/framework/__init__.py (in=170)
- tensorflow/core/framework/tensor.h (in=110)
- tensorflow/core/framework/logging.h (in=106)
- tensorflow/python/framework/ops.py (in=90)
- tensorflow/core/common_runtime/function.h (in=86)
- tensorflow/core/framework/op_kernel.h (in=80)
- tensorflow/core/common_runtime/device.h (in=79)
- tensorflow/core/framework/allocator.h (in=78)
- tensorflow/core/framework/op.h (in=70)
- tensorflow/core/framework/node_def_util.h (in=62)
- tensorflow/core/common_runtime/device_mgr.h (in=51)
- tensorflow/core/framework/tensor_shape.h (in=47)
- tensorflow/core/common_runtime/device_factory.h (in=45)
- tensorflow/core/framework/tensor_testutil.h (in=35)
- tensorflow/core/framework/device_base.h (in=34)
- tensorflow/core/common_runtime/graph_constructor.h (in=33)
- tensorflow/core/framework/node_def_builder.h (in=33)
- tensorflow/core/common_runtime/function_testlib.h (in=30)
- tensorflow/core/framework/collective.h (in=30)
- tensorflow/core/framework/cancellation.h (in=29)
- tensorflow/core/common_runtime/device/device_id.h (in=27)
- tensorflow/core/common_runtime/optimization_registry.h (in=24)
- tensorflow/python/eager/polymorphic_function/polymorphic_function.py (in=23)
- tensorflow/core/framework/rendezvous.h (in=22)

## Most called symbols
- tensorflow/python/framework/constant_op.py::constant (function, in=1055)
- tensorflow/python/framework/test_util.py::TensorFlowTestCase.assertAllEqual (function, in=480)
- tensorflow/python/framework/ops.py::Graph (class, in=404)
- tensorflow/python/eager/pywrap_tfe_src.cc::AccumulatorSet.end (function, in=381)
- tensorflow/python/eager/pywrap_tfe_src.cc::SafeSetCopy.end (function, in=381)
- tensorflow/core/common_runtime/propagator_state.h::TaggedNodeReadyQueue.push_back (function, in=377)
- tensorflow/core/common_runtime/propagator_state.h::OrderedPropagatorState.push_back (function, in=377)
- tensorflow/core/common_runtime/simple_propagator_state.h::tensorflow.TaggedNodeReadyQueue.push_back (function, in=377)
- tensorflow/core/framework/tensor_shape.cc::tensorflow.end (function, in=371)
- tensorflow/core/framework/tensor_shape_test.cc::tensorflow.end (function, in=370)
- tensorflow/core/framework/tensor_slice.h::tensorflow.TensorSlice.end (function, in=370)
- tensorflow/python/framework/errors_impl.py::InvalidArgumentError (class, in=345)
- tensorflow/python/eager/pywrap_tfe_src.cc::AccumulatorSet.empty (function, in=297)
- tensorflow/python/eager/pywrap_tfe_src.cc::SafeSetCopy.empty (function, in=297)
- tensorflow/core/framework/session_state.h::TensorStore.empty (function, in=292)
- tensorflow/python/eager/polymorphic_function/polymorphic_function_test.py::FunctionTest.testFunctionModifiesInputList.append (function, in=253)
- tensorflow/python/framework/tensor_shape.py::TensorShape (class, in=223)
- tensorflow/core/framework/tensor_shape.h::tensorflow.TensorShape (function, in=213)
- tensorflow/python/framework/errors_impl.py::InternalError (class, in=206)
- tensorflow/python/framework/test_util.py::TensorFlowTestCase.assertAllClose (function, in=205)
- tensorflow/python/framework/tensor.py::TensorSpec (class, in=192)
- tensorflow/python/framework/test_util.py::TensorFlowTestCase.evaluate (function, in=189)
- tensorflow/python/eager/backprop.py::GradientTape (class, in=168)
- tensorflow/python/eager/pywrap_tfe_src.cc::GradientTape (class, in=168)
- tensorflow/python/eager/pywrap_tfe_src.cc::GradientTape.GradientTape (function, in=168)