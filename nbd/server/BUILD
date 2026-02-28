package(default_visibility = ["//visibility:public"])

py_library(
    name = "server_lib",
    srcs = [
        "__init__.py",
        "connection.py",
        "handlers.py",
        "payloads.py",
        "protocol.py",
        "volume_minio.py",
    ],
    deps = ["//nbd:nbd"],
)

py_binary(
    name = "server",
    main = "main.py",
    srcs = ["main.py"],
    deps = ["//nbd:nbd", ":server_lib"],
)
