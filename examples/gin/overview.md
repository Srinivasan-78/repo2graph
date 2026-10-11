# Repo map: gin

files: 128  nodes: 2106  edges: 19552
languages: go=99, md=9, yml=8, yaml=3, tmpl=2, mod=1, sum=1, proto=1, txt=1

## Most depended-on files
- internal/bytesconv/bytesconv.go (in=8)
- codec/json/api.go (in=7)
- binding/binding.go (in=6)
- render/bson.go (in=3)
- testdata/protoexample/test.pb.go (in=3)
- auth.go (in=2)
- internal/fs/fs.go (in=2)

## Most called symbols
- gin.go::New (function, in=202)
- test_helpers.go::CreateTestContext (function, in=175)
- context.go::Context.Get (method, in=138)
- tree.go::Params.Get (method, in=134)
- context.go::Context.Set (method, in=101)
- routergroup.go::RouterGroup.GET (method, in=96)
- testdata/protoexample/test.pb.go::FOO.String (method, in=78)
- testdata/protoexample/test.pb.go::Test.String (method, in=78)
- testdata/protoexample/test.pb.go::Test_OptionalGroup.String (method, in=78)
- context.go::Context.String (method, in=74)
- errors.go::errorMsgs.String (method, in=73)
- routes_test.go::PerformRequest (function, in=73)
- binding/form_mapping.go::mappingByPtr (function, in=57)
- gin.go::Engine.Use (method, in=52)
- routergroup.go::RouterGroup.Use (method, in=52)
- binding/default_validator.go::SliceValidationError.Error (method, in=46)
- gin.go::Engine.ServeHTTP (method, in=45)
- utils_test.go::testStruct.ServeHTTP (method, in=45)
- binding/binding_test.go::requestWithBody (function, in=33)
- mode.go::SetMode (function, in=30)
- render/render_test.go::errorWriter.Header (method, in=29)
- tree.go::node.addRoute (method, in=26)
- context.go::Context.Next (method, in=25)
- gin.go::Engine.addRoute (method, in=25)
- gin.go::Engine.Run (method, in=24)