def decode_with_hex_fallback(
    data: bytes,
    *,
    hex_output: bool = False,
    display_ctrl_chars: bool = False,
) -> str:
    # 1️⃣ Hex mode
    if hex_output:
        return " ".join(f"{b:02X}" for b in data)

    out: list[str] = []
    i = 0

    while i < len(data):
        b = data[i]

        # ---------- ASCII (включно з control chars) ----------
        if b < 0x80:
            if b < 0x20 or b == 0x7F:
                # control chars
                if display_ctrl_chars:
                    out.append(f"\\x{b:02X}")
                else:
                    out.append(chr(b))
            else:
                out.append(chr(b))

            i += 1
            continue

        # ---------- UTF-8 ----------
        # Визначаємо довжину UTF-8 послідовності
        if (b & 0b11100000) == 0b11000000:
            seq_len = 2
        elif (b & 0b11110000) == 0b11100000:
            seq_len = 3
        elif (b & 0b11111000) == 0b11110000:
            seq_len = 4
        else:
            # Невалідний стартовий байт UTF-8
            out.append(f"<0x{b:02X}>")
            i += 1
            continue

        # Перевіряємо, чи вистачає байтів
        if i + seq_len > len(data):
            out.append(f"<0x{b:02X}>")
            i += 1
            continue

        # Спробуємо декодувати послідовність
        try:
            char = data[i:i + seq_len].decode("utf-8", errors="strict")
            out.append(char)
            i += seq_len
        except UnicodeDecodeError:
            # Якщо декодування не вдалося
            out.append(f"<0x{b:02X}>")
            i += 1

    return "".join(out)


# Тести
print("Тест 1 - нормальний текст:")
print(repr(decode_with_hex_fallback("привіт".encode("utf-8"))))

print("\nТест 2 - з битими байтами:")
test_broken = b"Hello \xff World \x80"
print(repr(decode_with_hex_fallback(test_broken)))

print("\nТест 3 - тільки биті байти:")
test_bad = b"\xff\xfe\xfd"
result = decode_with_hex_fallback(test_bad)
print(repr(result))
print(result)