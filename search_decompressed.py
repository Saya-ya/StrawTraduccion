import os

def search_text_in_files(directory, text):
    utf16 = text.encode('utf-16le')
    sjis = text.encode('shift-jis')
    
    print(f"Buscando '{text}'...")
    found = False
    
    for filename in os.listdir(directory):
        if not filename.endswith('.dec'):
            continue
            
        path = os.path.join(directory, filename)
        with open(path, 'rb') as f:
            data = f.read()
            
            idx = data.find(utf16)
            if idx != -1:
                print(f"  -> Encontrado (UTF-16) en {filename} en offset 0x{idx:0X}")
                found = True
                
            idx_sjis = data.find(sjis)
            if idx_sjis != -1:
                print(f"  -> Encontrado (SJIS) en {filename} en offset 0x{idx_sjis:0X}")
                found = True
                
    if not found:
        print("  -> No encontrado en los scripts.")

queries = [
    "桜の園の奥深くに",
    "汚れを知らない乙女たちが集う……",
    "乙女座の名・アストラエアを冠する",
    "３つの学びや。",
    "聖ミアトル女学園。",
    "明治期に創立された、",
    "由緒正しきお嬢様学校。"
]

for q in queries:
    search_text_in_files('work/scripts_extraidos', q)
