# NLSC地籍資訊查詢 / NLSC Cadastral Info

A QGIS plugin for querying Taiwan NLSC cadastral information, locating and coloring parcels, copying attributes to spreadsheets, and generating merged fill boundary vectors.

**Version:** 2.0.0 · **QGIS metadata compatibility:** 3.38–4.x · **License:** AGPL-3.0

## 功能 / Features

- 輸入單筆地號，或以半形逗號分隔多筆地號；也可點選地圖查詢宗地。
- 優先完成定位與填色，再補齊屬性資料；依範圍自動調整比例尺。
- 加入 NLSC 地籍圖，分別調整地籍圖顏色與宗地填色。
- 檢視土地、建物、測繪資訊及公有土地資料，視服務回傳內容顯示。
- 記錄多選、單筆或批次顏色設定、統一不透明度與數值地號排序。
- 複製記錄為 11 欄表格，每筆土地一列，可直接貼入 Excel。
- 將全部已顯示填色融合為單一「填色範圍」向量圖徵，置於圖層清單最上方。

Batch parcel queries, map-click lookup, extent-based location, independent map/fill colors, attribute inspection, record selection and sorting, spreadsheet copy, and merged boundary generation.

## 安裝 / Installation

1. Download the plugin ZIP from this project's Releases (when published).
2. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**.
3. Select the ZIP, install, and restart QGIS. Enable **NLSC地籍資訊查詢**.

QGIS：外掛 → 管理與安裝外掛 → 從 ZIP 安裝。正式上架官方外掛庫後亦可直接搜尋安裝。

The ZIP must contain a single `moi_parcel_locator/` directory with `metadata.txt` and `__init__.py`. No third-party Python packages need to be installed separately.

## 操作 / Usage

1. Select county, township, and land section. Enter parcel numbers such as `78, 78-1, 80`, then locate; or add the cadastral map and enable map-click lookup.
2. Review the records. Checkboxes control fill visibility; row selection controls batch actions and clipboard output. Click a color swatch to change colors.
3. Copy attributes to a spreadsheet, or generate the merged boundary.
4. Save a generated memory layer through **Export → Save Features As…**, for example to GeoPackage.

- [互動操作手冊 / Interactive guide](docs/index.html)：下載後以瀏覽器開啟；內含離線示範，不會查詢 NLSC。
- [PDF 圖文操作手冊 / PDF guide](docs/NLSC_User_Guide_v2.0.0.pdf)
- [版本更新 / Changelog](CHANGELOG.md)

## 資料來源與限制 / Data and limitations

Data source: **NLSC國土測繪圖資服務雲 / National Land Surveying and Mapping Center, Taiwan**.

- Taiwan only. Actual queries require Internet access. Service availability, returned attributes, imagery and request rate limits depend on NLSC.
- The plugin is independently developed and is not an official NLSC product. The code license does not grant additional rights to NLSC data or services.
- Generated vectors are derived from raster fill imagery. Their precision is limited by the source resolution; they are not authoritative survey boundaries.
- Fill processing and vector generation use memory. Public selection-list caching is bounded; QGIS tile caches and Python caches are managed by their respective runtimes.
- Records and memory layers are session data. Copy/export the results you need to retain.
- Release validation was performed on Windows with QGIS 4.2.3. Linux, macOS and QGIS 3.38 compatibility have not been separately validated; metadata version ranges are not a claim of testing every version.

## 問題回報 / Issues

Please use [GitHub Issues](https://github.com/LiXing183/NLSC-cadastral-info/issues). Include plugin/QGIS versions, operating system, steps to reproduce and the relevant error message. Remove personal information from screenshots or diagnostic files before submitting.

## 授權 / License

This repository follows the **GNU Affero General Public License v3.0** selected in the upstream repository. See [LICENSE](LICENSE). Third-party data and service terms remain with their respective providers.

Author: Xing Li, OpenAI.  
Maintainer email: li.xing.183.github@icloud.com
