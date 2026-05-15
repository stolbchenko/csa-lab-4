(setq buf-base 1792)
(setq n 5)
(setq received 0)
(setq done 0)

(defun _interrupt_handler ()
  (begin
    (setq c (read))
    (memset (+ buf-base received) c)
    (setq received (+ received 1))
    (if (= received n)
        (setq done 1)
        0)))

(ei)

(loop (while (= done 0)) 0)
(di)

(setq i 0)
(loop (while (< i n))
  (begin
    (setq j 0)
    (loop (while (< j (- n (+ i 1))))
      (begin
        (setq a (memref (+ buf-base j)))
        (setq b (memref (+ buf-base (+ j 1))))
        (if (> a b)
            (begin
              (memset (+ buf-base j) b)
              (memset (+ buf-base (+ j 1)) a))
            0)
        (setq j (+ j 1))))
    (setq i (+ i 1))))

(setq i 0)
(loop (while (< i n))
  (begin
    (print (memref (+ buf-base i)))
    (if (< i (- n 1))
        (begin
          (print-char 44)
          (print-char 32))
        0)
    (setq i (+ i 1))))
(print-char 10)
