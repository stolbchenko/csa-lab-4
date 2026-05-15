(defun factorial (n)
  (if (= n 0)
      1
      (* n (factorial (- n 1)))))

(print (factorial 5))
(print-char 10)
