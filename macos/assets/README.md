# Tulpa Mac 图标

`tulpa-icon-original.png` 是原始标志图片，保持字节不变。
`Tulpa-icon-rounded.png` 是以原标志为参考制作的圆角黑底图标，外部为透明边缘。`Tulpa-icon.png` 和 `Tulpa.icns` 是它的应用资源版本，封装时保留透明通道，避免运行中的 Dock 图标变成黑色方块。

在项目目录重新生成图标：

```sh
.venv/bin/python macos/build_icon.py
```

macOS 的 `iconutil` 需要能够访问系统图像转换服务。可以添加 `--install-app /完整路径/Tulpa.app` 更新已有应用的图标；此操作只更新图标资源和 `CFBundleIconFile`，不会重启 Tulpa 或 Dock。Finder 和 Dock 的旧图标缓存可能需要下一次正常打开应用才更新。

`macos/build_app.py` 会将 `Tulpa.icns` 自动放入新应用包。
