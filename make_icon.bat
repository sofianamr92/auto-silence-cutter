@echo off
rem -------------------------------------------------
rem  إنشاء أيقونة icon.ico من الصورة المرفقة
rem -------------------------------------------------

rem تأكيد وجود مجلد assets (سيُنشأ إذا لم يكن موجودًا)
if not exist "D:\مونتاج-\assets" mkdir "D:\مونتاج-\assets"

rem تثبيت مكتبة Pillow إذا لم تكن موجودة
pip show pillow >nul 2>&1
if errorlevel 1 (
    echo تثبيت مكتبة Pillow...
    pip install pillow
)

rem تشغيل سكربت Python
python "D:\مونتاج-\make_icon.py"
if %errorlevel% neq 0 (
    echo فشل إنشاء الأيقونة. راجع الرسالة أعلاه.
    exit /b 1
)

echo تم إنشاء الأيقونة بنجاح.
pause