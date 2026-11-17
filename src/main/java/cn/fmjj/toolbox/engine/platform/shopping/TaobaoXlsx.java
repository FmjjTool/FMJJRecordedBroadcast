package cn.fmjj.toolbox.engine.platform.shopping;

import org.apache.poi.ss.usermodel.CellStyle;
import org.apache.poi.ss.usermodel.ClientAnchor;
import org.apache.poi.ss.usermodel.FillPatternType;
import org.apache.poi.ss.usermodel.Font;
import org.apache.poi.ss.usermodel.IndexedColors;
import org.apache.poi.ss.usermodel.Row;
import org.apache.poi.xssf.usermodel.XSSFClientAnchor;
import org.apache.poi.xssf.usermodel.XSSFDrawing;
import org.apache.poi.xssf.usermodel.XSSFPicture;
import org.apache.poi.xssf.usermodel.XSSFSheet;
import org.apache.poi.xssf.usermodel.XSSFWorkbook;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * 单商品 Excel 导出（对应 Python 版 openpyxl 五 sheet + 图片嵌入）。
 */
public final class TaobaoXlsx {

    private TaobaoXlsx() {}

    public static String export(Shopping.TaobaoItemDetail detail, String path) throws IOException {
        try (XSSFWorkbook wb = new XSSFWorkbook()) {
            CellStyle head = headStyle(wb);

            List<String[]> infoRows = new ArrayList<>();
            add(infoRows, "标题", detail.title);
            add(infoRows, "价格", detail.price);
            add(infoRows, "销量", detail.sales);
            add(infoRows, "店铺", detail.shopName);
            add(infoRows, "卖家ID", detail.sellerId);
            add(infoRows, "店铺ID", detail.shopId);
            add(infoRows, "商品ID", detail.itemId);
            add(infoRows, "链接", detail.url);
            add(infoRows, "物流", detail.delivery);
            add(infoRows, "店铺评分", detail.shopScores);
            add(infoRows, "主图数量", String.valueOf(
                    detail.localMain.isEmpty() ? detail.mainImages.size() : detail.localMain.size()));
            add(infoRows, "规格图数量", String.valueOf(detail.localSku.size()));
            add(infoRows, "图文详情图数量", String.valueOf(
                    detail.localDesc.isEmpty() ? detail.descImages.size() : detail.localDesc.size()));
            add(infoRows, "获取时间", detail.fetchedAt);
            table(wb, "商品信息", new String[]{"项目", "内容"}, infoRows,
                    new int[]{18, 60}, head);

            List<String[]> allParams = new ArrayList<>();
            allParams.add(new String[]{"商品", "标题", detail.title});
            if (!detail.price.isEmpty()) allParams.add(new String[]{"商品", "价格", detail.price});
            if (!detail.sales.isEmpty()) allParams.add(new String[]{"商品", "销量", detail.sales});
            if (!detail.shopName.isEmpty()) allParams.add(new String[]{"商品", "店铺", detail.shopName});
            if (!detail.sellerId.isEmpty()) allParams.add(new String[]{"商品", "卖家ID", detail.sellerId});
            if (!detail.shopId.isEmpty()) allParams.add(new String[]{"商品", "店铺ID", detail.shopId});
            if (!detail.url.isEmpty()) allParams.add(new String[]{"商品", "链接", detail.url});
            for (com.fasterxml.jackson.databind.node.ObjectNode grp : detail.skuProps) {
                List<String> vals = new ArrayList<>();
                for (com.fasterxml.jackson.databind.JsonNode v : grp.path("values")) {
                    vals.add(v.path("name").asText(""));
                }
                if (!vals.isEmpty()) {
                    allParams.add(new String[]{"规格参数", grp.path("name").asText("规格"),
                            String.join(" / ", vals)});
                }
            }
            for (String[] p : detail.params) allParams.add(new String[]{"参数", p[0], p[1]});
            if (!detail.delivery.isEmpty()) allParams.add(new String[]{"物流", "发货/运费", detail.delivery});
            if (!detail.shopScores.isEmpty()) allParams.add(new String[]{"店铺", "评分", detail.shopScores});
            table(wb, "参数信息(全部)", new String[]{"类别", "参数", "值"}, allParams,
                    new int[]{12, 24, 80}, head);

            List<String[]> skuRows = new ArrayList<>();
            for (Shopping.ItemSku s : detail.skus) {
                skuRows.add(new String[]{s.names, s.price, s.quantity});
            }
            if (skuRows.isEmpty()) skuRows.add(new String[]{"（单规格商品）", detail.price, ""});
            table(wb, "选购参数", new String[]{"选项", "价格", "库存"}, skuRows,
                    new int[]{56, 18, 14}, head);

            imageSheet(wb, "主图", detail.localMain, detail.mainImages, head);
            imageSheet(wb, "图文详情", detail.localDesc, detail.descImages, head);

            Path out = Path.of(path);
            if (out.getParent() != null) Files.createDirectories(out.getParent());
            try (OutputStream os = Files.newOutputStream(out)) {
                wb.write(os);
            }
        }
        return path;
    }

    private static CellStyle headStyle(XSSFWorkbook wb) {
        CellStyle style = wb.createCellStyle();
        style.setFillForegroundColor(IndexedColors.LEMON_CHIFFON.getIndex());
        style.setFillPattern(FillPatternType.SOLID_FOREGROUND);
        Font font = wb.createFont();
        font.setBold(true);
        style.setFont(font);
        return style;
    }

    private static void table(XSSFWorkbook wb, String name, String[] headers,
                              List<String[]> rows, int[] widths, CellStyle head) {
        XSSFSheet ws = wb.createSheet(name);
        Row h = ws.createRow(0);
        for (int i = 0; i < headers.length; i++) {
            var cell = h.createCell(i);
            cell.setCellValue(headers[i]);
            cell.setCellStyle(head);
        }
        for (int r = 0; r < rows.size(); r++) {
            Row row = ws.createRow(r + 1);
            String[] values = rows.get(r);
            for (int c = 0; c < values.length; c++) {
                row.createCell(c).setCellValue(values[c] == null ? "" : values[c]);
            }
        }
        for (int c = 0; c < headers.length; c++) {
            int w = c < widths.length ? widths[c] : 30;
            ws.setColumnWidth(c, w * 256);
        }
        ws.createFreezePane(0, 1);
    }

    private static void imageSheet(XSSFWorkbook wb, String name, List<String> local,
                                   List<String> urls, CellStyle head) {
        XSSFSheet ws = wb.createSheet(name);
        ws.setColumnWidth(0, 56 * 256);
        ws.setColumnWidth(1, 46 * 256);
        Row h = ws.createRow(0);
        var c0 = h.createCell(0);
        c0.setCellValue("图片");
        c0.setCellStyle(head);
        var c1 = h.createCell(1);
        c1.setCellValue("说明");
        c1.setCellStyle(head);
        ws.createFreezePane(0, 1);
        if (local.isEmpty()) {
            h.getCell(1).setCellValue("（图片未下载，仅列 URL）");
            for (int i = 0; i < urls.size(); i++) {
                ws.createRow(i + 1).createCell(1).setCellValue(urls.get(i));
            }
            return;
        }
        XSSFDrawing drawing = ws.createDrawingPatriarch();
        int rowIdx = 1;
        for (int i = 0; i < local.size(); i++) {
            String p = local.get(i);
            Row row = ws.createRow(rowIdx);
            try {
                BufferedImage img = ImageIO.read(new File(p));
                if (img == null) throw new IOException("无法读取图片");
                int w0 = Math.max(img.getWidth(), 1);
                int h0 = Math.max(img.getHeight(), 1);
                int targetW = 380;
                int targetH = Math.max(1, (int) (h0 * 380.0 / w0));
                row.setHeightInPoints((float) (targetH * 0.75 + 8));
                byte[] data;
                int type;
                String ext = Shopping.imgExt(p);
                if (".jpg".equals(ext) || ".png".equals(ext) || ".gif".equals(ext)) {
                    data = Files.readAllBytes(Path.of(p));
                    type = ".png".equals(ext) ? XSSFWorkbook.PICTURE_TYPE_PNG
                            : ".gif".equals(ext) ? XSSFWorkbook.PICTURE_TYPE_GIF
                            : XSSFWorkbook.PICTURE_TYPE_JPEG;
                } else {
                    data = pngBytes(img);
                    type = XSSFWorkbook.PICTURE_TYPE_PNG;
                }
                int pictureIdx = wb.addPicture(data, type);
                XSSFClientAnchor anchor = new XSSFClientAnchor(0, 0, 0, 0, 0, rowIdx, 0, rowIdx);
                anchor.setAnchorType(ClientAnchor.AnchorType.MOVE_AND_RESIZE);
                XSSFPicture picture = drawing.createPicture(anchor, pictureIdx);
                picture.resize(targetW / (double) w0);
                row.createCell(1).setCellValue((i + 1) + "/" + local.size());
            } catch (Exception e) {
                row.createCell(1).setCellValue("图片插入失败：" + Path.of(p).getFileName());
            }
            rowIdx++;
        }
    }

    private static byte[] pngBytes(BufferedImage img) throws IOException {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        ImageIO.write(img, "png", out);
        return out.toByteArray();
    }

    private static void add(List<String[]> rows, String key, String value) {
        if (value != null && !value.isEmpty()) rows.add(new String[]{key, value});
    }
}
