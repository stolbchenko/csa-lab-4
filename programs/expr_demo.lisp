(setq x 1)

(print (if (= x 1) "yes" "no"))
(print-char 10)

(setq x (if (= x 1) 42 0))
(print x)
(print-char 10)

(setq i 0)
(setq result (loop (while (< i 3)) (setq i (+ i 1))))
(print result)
(print-char 10)

(print (setq x 13))
(print-char 10)
