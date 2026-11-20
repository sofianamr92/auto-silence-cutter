# make_icon.py
# تحويل صورة JPEG إلى ملف ICO يحتوي على أحجام 256، 48، 32، 16 بكسل
# يُستدعى من ملف batch في مجلد D:\مونتاج-

import sys
from pathlib import Path
from PIL import Image

# ---------- ضبط المسارات ----------
# مسار الصورة التي رفعتها
jpeg_path = Path(r"D:\مونتاج-\media_1795083616624_e0765c4f.jpg")
# مسار الأيقونة التي ستُنشأ
ico_path = Path(r"D:\مونتاج-\assets\icon.ico")

# ---------- فحص وجود الملف ----------
if not jpeg_path.is_file():
    sys.exit(f"❌ ملف JPEG غير موجود: {jpeg_path}")

# ---------- إنشاء مجلد assets إذا لم يكن موجودًا ----------
ico_path.parent.mkdir(parents=True, exist_ok=True)

# ---------- تحويل الصورة ----------
# أحجام الأيقونة المطلوبة (المكتبة تدعم حفظ عدة أحجام داخل ملف ICO واحد)
sizes = [(256, 256), (48, 48), (32, 32), (16, 16)]

# احفظ كـ ICO مع الأحجام المحددة
with Image.open(jpeg_path).convert("RGBA") as im:
    im.save(ico_path, format="ICO", sizes=sizes)

print(f"✅ تم إنشاء الأيقونة بنجاح: {ico_path}")