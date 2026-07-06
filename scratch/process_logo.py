import os
import sys
import subprocess

# Ensure Pillow is installed
try:
    from PIL import Image, ImageChops
except ImportError:
    print("Pillow not found, installing...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow"])
    from PIL import Image, ImageChops

def remove_background_and_crop(input_path, output_path):
    print(f"Processing {input_path}...")
    img = Image.open(input_path).convert("RGBA")
    
    # Replace white-ish pixels with transparency
    datas = img.getdata()
    newData = []
    for item in datas:
        # Check if the pixel is close to white (threshold 240 out of 255)
        if item[0] > 240 and item[1] > 240 and item[2] > 240:
            # Make it fully transparent
            newData.append((255, 255, 255, 0))
        else:
            newData.append(item)
    
    img.putdata(newData)
    
    # Auto-crop to the bounding box of non-transparent pixels
    # Find the bounding box
    alpha = img.split()[-1]
    bbox = alpha.getbbox()
    if bbox:
        img_cropped = img.crop(bbox)
        # Add a small padding of 10px around the logo
        padding = 10
        width, height = img_cropped.size
        padded_img = Image.new("RGBA", (width + padding * 2, height + padding * 2), (255, 255, 255, 0))
        padded_img.paste(img_cropped, (padding, padding))
        
        # Save output
        padded_img.save(output_path, "PNG")
        print(f"Successfully saved cropped transparent logo to {output_path}")
    else:
        img.save(output_path, "PNG")
        print(f"Saved full transparent logo (no crop) to {output_path}")

if __name__ == "__main__":
    src_dir = "C:\\Users\\DELL\\.gemini\\antigravity\\brain\\f384e257-3f4e-46f6-a173-0ef22f36ac3a"
    filename = "media__1783333602415.png"
    src_path = os.path.join(src_dir, filename)
    
    dest_dir = "c:\\Users\\DELL\\Downloads\\AI_resume_copilot\\frontend\\public"
    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, "logo.png")
    
    remove_background_and_crop(src_path, dest_path)
