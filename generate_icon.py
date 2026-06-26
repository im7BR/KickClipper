import os
from PIL import Image, ImageDraw

def generate_ico():
    print("Generating application icon...")
    # Sizes standard for Windows .ico files
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    
    # We will generate a high-res 256x256 image and resize it for the other icons
    base_img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base_img)
    
    # 1. Dark background rounded square
    # Using HSL/Tailwind-like dark grey-blue color
    draw.rounded_rectangle([8, 8, 248, 248], radius=48, fill=(15, 15, 26, 255), outline=(83, 252, 24, 30), width=4)
    
    # 2. Glowing green outline
    draw.rounded_rectangle([12, 12, 244, 244], radius=44, fill=None, outline=(83, 252, 24, 20), width=8)
    
    # 3. Draw a play button green circle
    draw.ellipse([58, 58, 198, 198], fill=(83, 252, 24, 255))
    
    # 4. Draw a dark grey "K" in the middle of the play button
    # Using polygon lines to draw a bold 'K' letter
    draw.polygon([
        (90, 80), (108, 80), 
        (108, 176), (90, 176)
    ], fill=(10, 10, 15, 255)) # vertical bar
    
    draw.polygon([
        (108, 128), (145, 80),
        (165, 80), (120, 138)
    ], fill=(10, 10, 15, 255)) # upper arm
    
    draw.polygon([
        (116, 132), (155, 176),
        (175, 176), (128, 124)
    ], fill=(10, 10, 15, 255)) # lower arm
    
    # Save the multiple-size icon
    icon_path = "logo.ico"
    base_img.save(icon_path, format="ICO", sizes=sizes)
    print(f"Icon generated successfully at {os.path.abspath(icon_path)}")

if __name__ == "__main__":
    generate_ico()
