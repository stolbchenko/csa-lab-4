(defun __print_pstr (pstr-addr)
  (begin
    (setq __pstr-len (memref pstr-addr))
    (setq __pstr-i 0)
    (loop (while (< __pstr-i __pstr-len))
      (begin
        (print-char (memref (+ pstr-addr (+ 1 __pstr-i))))
        (setq __pstr-i (+ __pstr-i 1))))))
