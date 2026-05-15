(setq digits 0)
(setq done 0)

(defun _interrupt_handler ()
  (begin
    (setq c (read))
    (if (= c 10)
        (setq done 1)
        (setq digits (+ (* digits 10) (- c 48))))))

(print "Digits: ")
(ei)

(loop (while (= done 0)) 0)
(di)

(print digits)
(print-char 10)

(setq lo 1)
(setq k 1)
(loop (while (< k digits))
  (begin
    (setq lo (* lo 10))
    (setq k (+ k 1))))

(setq hi (- (* lo 10) 1))
(setq best 0)

(defun palindrome? (n)
  (begin
    (setq orig n)
    (setq rev 0)
    (loop (while (> n 0))
      (begin
        (setq rev (+ (* rev 10) (mod n 10)))
        (setq n (/ n 10))))
    (if (= rev orig) 1 0)))

(setq i hi)
(loop (while (and (>= i lo) (> (* i hi) best)))
  (begin
    (setq j hi)
    (loop (while (and (>= j i) (> (* i j) best)))
      (begin
        (setq prod (* i j))
        (if (palindrome? prod)
            (setq best prod)
            0)
        (setq j (- j 1))))
    (setq i (- i 1))))

(print "Largest palindrome product: ")
(print best)
(print-char 10)
